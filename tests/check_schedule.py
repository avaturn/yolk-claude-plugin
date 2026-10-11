"""schedule: heal, install, the SessionStart hook."""
import contextlib
import io
import json
import os
import plistlib
import shutil
import sys
import tempfile
from pathlib import Path

from support import CFG, clean_dirs, agent_log
from yolk import api, cli, config, poll, schedule


def check_heal_schedule():
    clean_dirs()
    tmp = Path(tempfile.mkdtemp(prefix="yolk-plist-"))
    real_plist, real_install = config.LAUNCH_PLIST, schedule.install_schedule
    config.LAUNCH_PLIST = tmp / "com.yolk.agent.plist"  # never the real ~/Library/LaunchAgents
    calls = []
    schedule.install_schedule = lambda: (calls.append(1), result[0])[1]
    result = [True]

    real_root, real_plugins = config.PLUGIN_ROOT, config.INSTALLED_PLUGINS_DIR
    config.INSTALLED_PLUGINS_DIR = tmp / "plugins"           # pretend this copy is the installed plugin
    config.PLUGIN_ROOT = config.INSTALLED_PLUGINS_DIR / "cache" / "yolk"

    def plist(*args, home=None, schedule=None):
        config.LAUNCH_PLIST.write_bytes(plistlib.dumps({
            "Label": "com.yolk.agent", "ProgramArguments": list(args),
            "EnvironmentVariables": {"YOLK_HOME": home or str(config.YOLK_HOME)},
            **(schedule if schedule is not None else {"StartCalendarInterval": [{"Minute": m} for m in (0, 20, 40)]})}))

    xpc = os.environ.pop("XPC_SERVICE_NAME", None)
    try:
        schedule.heal_schedule()                                                # no plist, not linked: nothing is installed
        assert not calls and not config.LAUNCH_PLIST.exists()
        launcher = config.YOLK_HOME / "bin" / "launcher.py"
        launcher.parent.mkdir(exist_ok=True)
        launcher.write_bytes((config.SCRIPTS_DIR / "launcher.py").read_bytes())      # the installed copy is current
        plist("/usr/bin/python3", str(launcher))                          # current: untouched
        schedule.heal_schedule()
        assert not calls
        link = tmp / "linked-bin"
        link.symlink_to(launcher.parent)
        plist("/usr/bin/python3", str(link / "launcher.py"))              # same file through a symlink: current
        schedule.heal_schedule()
        assert not calls
        # a launcher copy that differs from this version's is re-copied (install re-runs)
        plist("/usr/bin/python3", str(launcher))
        launcher.write_text("# an older launcher\n")
        schedule.heal_schedule()
        assert len(calls) == 1 and "outdated launcher" in agent_log()
        calls.clear()
        launcher.write_bytes((config.SCRIPTS_DIR / "launcher.py").read_bytes())
        # a 1.6.3 plist (launcher path + StartInterval) migrates to the calendar schedule
        n0 = len(calls)
        plist("/usr/bin/python3", str(launcher), schedule={"StartInterval": 1200})
        schedule.heal_schedule()
        assert len(calls) == n0 + 1 and "used the old schedule" in agent_log()
        plist("/usr/bin/python3", str(launcher), schedule={"StartInterval": 1200, "StartCalendarInterval": [{"Minute": 0}]})
        schedule.heal_schedule()
        assert len(calls) == n0 + 2                                           # both keys: still old
        plist("/usr/bin/python3", str(launcher), schedule={})
        schedule.heal_schedule()
        assert len(calls) == n0 + 3                                           # neither key
        plist("/usr/bin/python3", str(launcher))
        schedule.heal_schedule()
        assert len(calls) == n0 + 3                                           # current: untouched
        calls[:] = calls[:n0]
        before = agent_log()
        # a version-pinned plist (1.3 - 1.6.0) migrates to the launcher
        plist("/usr/bin/python3", "/old/cache/yolk/yolk/1.3.1/scripts/employee_runner.py")  # stale
        os.environ["XPC_SERVICE_NAME"] = "com.yolk.agent"                 # started by launchd itself: never reinstall
        schedule.heal_schedule()
        assert not calls
        del os.environ["XPC_SERVICE_NAME"]
        real_ppid, os.getppid = os.getppid, lambda: 1                    # parent is launchd (pid 1): same
        try:
            schedule.heal_schedule()
        finally:
            os.getppid = real_ppid
        assert not calls
        schedule.heal_schedule()
        assert len(calls) == 1
        added = agent_log()[len(before):].strip().splitlines()
        assert len(added) == 1 and "did not use the launcher" in added[0], added          # exactly one log line
        # a dev/test run must never re-point the real poller (install_schedule.sh would bake in ITS env)
        plist("/old/x/employee_runner.py", home="/some/other/yolk-home")   # the plist belongs to another YOLK_HOME
        schedule.heal_schedule()
        assert len(calls) == 1
        plist("/old/x/employee_runner.py")
        config.PLUGIN_ROOT = real_root                                          # this copy is not under ~/.claude/plugins
        schedule.heal_schedule()
        assert len(calls) == 1
        config.PLUGIN_ROOT = config.INSTALLED_PLUGINS_DIR / "cache" / "yolk"
        config.LAUNCH_PLIST.write_text("not a plist")                           # unreadable: cannot prove it is ours
        schedule.heal_schedule()
        assert len(calls) == 1 and "plist unreadable: run install_schedule.sh" in agent_log()
        plist("/old/x/employee_runner.py")
        schedule.heal_schedule()
        assert len(calls) == 2
        result[0] = False                                                  # a failing install is logged, not raised
        schedule.heal_schedule()
        assert len(calls) == 3 and "could not update it" in agent_log()
        # wired into main() for every action but leave
        seen = []
        real_heal, schedule.heal_schedule = schedule.heal_schedule, lambda: seen.append(1)
        real_cfg, config.load_config = config.load_config, lambda: CFG
        real_sync, real_hb, real_leave = poll.sync_briefs, poll.heartbeat, cli.leave
        poll.sync_briefs = poll.heartbeat = cli.leave = lambda *a, **k: None
        try:
            for action in ("sync", "leave"):
                sys.argv = ["employee_runner.py", action]
                cli.main()
            assert seen == [1], seen
        finally:
            schedule.heal_schedule, config.load_config = real_heal, real_cfg
            poll.sync_briefs, poll.heartbeat, cli.leave = real_sync, real_hb, real_leave
    finally:
        config.LAUNCH_PLIST, schedule.install_schedule = real_plist, real_install
        config.PLUGIN_ROOT, config.INSTALLED_PLUGINS_DIR = real_root, real_plugins
        os.environ.pop("XPC_SERVICE_NAME", None)
        if xpc is not None:
            os.environ["XPC_SERVICE_NAME"] = xpc

def check_heal_installs_missing_plist():
    clean_dirs()
    tmp = Path(tempfile.mkdtemp(prefix="yolk-plist-"))
    saved = (config.LAUNCH_PLIST, schedule.install_schedule, config.PLUGIN_ROOT, config.INSTALLED_PLUGINS_DIR, config.DEFAULT_YOLK_HOME)
    env_home = os.environ.get("YOLK_HOME")
    config.LAUNCH_PLIST = tmp / "com.yolk.agent.plist"  # never the real ~/Library/LaunchAgents
    config.INSTALLED_PLUGINS_DIR = tmp / "plugins"
    good_root = config.INSTALLED_PLUGINS_DIR / "cache" / "yolk"
    config.PLUGIN_ROOT, config.DEFAULT_YOLK_HOME = good_root, config.YOLK_HOME  # the temp home plays the default home
    calls = []
    schedule.install_schedule = lambda: (calls.append(1), True)[1]
    xpc = os.environ.pop("XPC_SERVICE_NAME", None)

    def attempt(expect, why):
        before = len(calls)
        schedule.heal_schedule()
        assert len(calls) - before == expect, why

    try:
        config.CONFIG_FILE.unlink(missing_ok=True)
        attempt(0, "not linked (before claim wrote the config, or after leave)")
        config.CONFIG_FILE.write_text("{}")
        attempt(1, "linked + installed copy + default home + not launchd: install the missing plist")
        os.environ["YOLK_HOME"] = str(tmp / "elsewhere")
        attempt(0, "YOLK_HOME env overridden")
        os.environ["YOLK_HOME"] = env_home
        config.DEFAULT_YOLK_HOME = tmp / "not-the-home"
        attempt(0, "YOLK_HOME is not the default home")
        config.DEFAULT_YOLK_HOME = config.YOLK_HOME
        config.PLUGIN_ROOT = saved[2]
        attempt(0, "running from a non-installed copy (dev checkout)")
        config.PLUGIN_ROOT = good_root
        os.environ["XPC_SERVICE_NAME"] = "com.yolk.agent"
        attempt(0, "launched by launchd")
        del os.environ["XPC_SERVICE_NAME"]
        # after leave: agent.json is gone, so nothing brings the plist back
        config.CONFIG_FILE.unlink()
        attempt(0, "after leave")
        # leave itself never heals
        config.CONFIG_FILE.write_text("{}")
        seen = []
        real_heal, schedule.heal_schedule = schedule.heal_schedule, lambda: seen.append(1)
        real_cfg, config.load_config, real_leave, cli.leave = config.load_config, lambda: CFG, cli.leave, lambda cfg: config.CONFIG_FILE.unlink()
        try:
            sys.argv = ["employee_runner.py", "leave"]
            cli.main()
        finally:
            schedule.heal_schedule, config.load_config, cli.leave = real_heal, real_cfg, real_leave
        assert not seen and not config.CONFIG_FILE.exists()
        attempt(0, "after leave, a later run")
    finally:
        config.LAUNCH_PLIST, schedule.install_schedule, config.PLUGIN_ROOT, config.INSTALLED_PLUGINS_DIR, config.DEFAULT_YOLK_HOME = saved
        config.CONFIG_FILE.unlink(missing_ok=True)
        if env_home is not None:
            os.environ["YOLK_HOME"] = env_home
        if xpc is not None:
            os.environ["XPC_SERVICE_NAME"] = xpc

def check_install_script_plist():
    """Run the real install_schedule.sh under a temp HOME with a stub launchctl: never the real launchd."""
    import subprocess
    home = Path(tempfile.mkdtemp(prefix="yolk-home-"))
    stub = home / "bin"
    stub.mkdir()
    (stub / "launchctl").write_text("#!/bin/sh\necho \"$@\" >> \"$HOME/launchctl.log\"\n")
    (stub / "launchctl").chmod(0o755)
    env = {"HOME": str(home), "PATH": f"{stub}:/usr/bin:/bin:{Path(sys.executable).parent}", "YOLK_HOME": str(home / ".claude" / "yolk")}

    def install(interval=None):
        agent = home / ".claude" / "yolk" / "agent.json"
        agent.parent.mkdir(parents=True, exist_ok=True)
        agent.write_text(json.dumps({} if interval is None else {"poll_interval_s": interval}))
        p = subprocess.run(["bash", str(config.SCRIPTS_DIR / "install_schedule.sh")], env=env, capture_output=True, text=True, timeout=60)
        assert p.returncode == 0, p.stderr
        return plistlib.loads((home / "Library" / "LaunchAgents" / "com.yolk.agent.plist").read_bytes())

    def minutes(plist):
        return [d["Minute"] for d in plist["StartCalendarInterval"]]
    plist = install()
    assert minutes(plist) == [0, 20, 40] and "StartInterval" not in plist         # wake-proof calendar schedule
    assert minutes(install(600)) == [0, 10, 20, 30, 40, 50]
    assert minutes(install(3600)) == [0] and minutes(install(7200)) == [0]       # an hour or more: hourly
    assert minutes(install(1500)) == [0] and minutes(install(45)) == [0]         # not a divisor of 3600: hourly
    plist = install()
    assert plist["RunAtLoad"] is True and plist["Label"] == "com.yolk.agent"
    assert plist["EnvironmentVariables"]["YOLK_HOME"] == env["YOLK_HOME"]
    launcher = home / ".claude" / "yolk" / "bin" / "launcher.py"
    assert plist["ProgramArguments"][1] == str(launcher)            # the plist runs the stable launcher
    assert launcher.read_bytes() == (config.SCRIPTS_DIR / "launcher.py").read_bytes()   # ...and it was copied
    assert "employee_runner.py" not in json.dumps(plist["ProgramArguments"])
    assert "load" in (home / "launchctl.log").read_text()      # went to the stub, not the real launchd
    shutil.rmtree(home, ignore_errors=True)

def check_heal_action_and_hook():
    clean_dirs()
    hook = json.loads((config.PLUGIN_ROOT / "hooks" / "hooks.json").read_text())["hooks"]["SessionStart"][0]
    cmd = hook["hooks"][0]
    assert cmd["type"] == "command" and cmd["command"].endswith('employee_runner.py" heal || true') and cmd["timeout"] <= 30
    assert "${CLAUDE_PLUGIN_ROOT}" in cmd["command"]

    tmp = Path(tempfile.mkdtemp(prefix="yolk-heal-"))
    saved = (config.LAUNCH_PLIST, schedule.install_schedule, config.PLUGIN_ROOT, config.INSTALLED_PLUGINS_DIR, config.DEFAULT_YOLK_HOME)
    config.LAUNCH_PLIST = tmp / "com.yolk.agent.plist"
    config.INSTALLED_PLUGINS_DIR = tmp / "plugins"
    good_root = config.INSTALLED_PLUGINS_DIR / "cache" / "yolk"
    config.PLUGIN_ROOT, config.DEFAULT_YOLK_HOME = good_root, config.YOLK_HOME
    calls = []
    ok = [True]
    schedule.install_schedule = lambda: (calls.append(1), ok[0])[1]
    launcher = config.YOLK_HOME / "bin" / "launcher.py"
    launcher.parent.mkdir(exist_ok=True)
    launcher.write_bytes((config.SCRIPTS_DIR / "launcher.py").read_bytes())
    xpc = os.environ.pop("XPC_SERVICE_NAME", None)
    network = []
    real_api, api.request = api.request, lambda *a, **k: network.append(a) or (200, {})

    def heal():
        buf = io.StringIO()
        sys.argv = ["employee_runner.py", "heal"]
        with contextlib.redirect_stdout(buf):
            cli.main()                      # must return normally, whatever happens
        return buf.getvalue().strip().splitlines()

    def plist(*args):
        config.LAUNCH_PLIST.write_bytes(plistlib.dumps({"ProgramArguments": list(args),
                                                   "EnvironmentVariables": {"YOLK_HOME": str(config.YOLK_HOME)},
                                                   "StartCalendarInterval": [{"Minute": m} for m in (0, 20, 40)]}))
    try:
        config.CONFIG_FILE.unlink(missing_ok=True)
        assert heal() == [] and not calls                                  # not linked: silent, nothing installed
        config.CONFIG_FILE.write_text("{}")
        plist("/usr/bin/python3", str(launcher))
        assert heal() == [] and not calls                                  # nothing to change: silent
        plist("/usr/bin/python3", "/old/cache/yolk/yolk/1.6.0/scripts/employee_runner.py")
        out = heal()                                                       # old versioned plist: one line
        assert len(calls) == 1 and len(out) == 1 and "launcher" in out[0], out
        ok[0] = False
        assert len(heal()) == 1                                            # a failing install: still one line, no exception
        config.LAUNCH_PLIST.write_text("junk")
        assert heal() == []                                                # unreadable plist: logged to the file only
        assert "plist unreadable" in agent_log()
        plist("/old/x/employee_runner.py")
        config.PLUGIN_ROOT = saved[2]                                           # guards hold: non-installed copy
        n = len(calls)
        assert heal() == [] and len(calls) == n
        config.PLUGIN_ROOT = good_root
        os.environ["YOLK_HOME"], old_env = str(tmp / "else"), os.environ["YOLK_HOME"]
        config.LAUNCH_PLIST.unlink()
        assert heal() == [] and len(calls) == n                            # YOLK_HOME overridden: no install
        os.environ["YOLK_HOME"] = old_env
        schedule.install_schedule = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
        assert heal() == []                                                # an exception never escapes
        assert not network                                                 # no network call at all
    finally:
        api.request = real_api
        config.LAUNCH_PLIST, schedule.install_schedule, config.PLUGIN_ROOT, config.INSTALLED_PLUGINS_DIR, config.DEFAULT_YOLK_HOME = saved
        config.CONFIG_FILE.unlink(missing_ok=True)
        os.environ["YOLK_HOME"] = str(config.YOLK_HOME)
        if xpc is not None:
            os.environ["XPC_SERVICE_NAME"] = xpc
