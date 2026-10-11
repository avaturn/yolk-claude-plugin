"""launcher: the stable entry for launchd."""
import json
import os
import sys
import tempfile
from pathlib import Path

from yolk import config


def check_launcher():
    import importlib.util
    spec = importlib.util.spec_from_file_location("yolk_launcher", config.SCRIPTS_DIR / "launcher.py")
    L = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(L)
    plugins = Path(tempfile.mkdtemp(prefix="yolk-cache-"))
    assert L.find_runner(plugins) is None                                       # no cache at all
    cache = plugins / "cache" / "yolk" / "yolk"
    cache.mkdir(parents=True)
    assert L.find_runner(plugins) is None                                       # empty cache

    def make(version, orphaned=False, runner=True):
        d = cache / version
        (d / "scripts").mkdir(parents=True)
        if runner:
            (d / "scripts" / "employee_runner.py").write_text("")
        if orphaned:
            (d / ".orphaned_at").write_text("1")
        return d
    make("1.5.0"); make("1.6.0", orphaned=True); make("1.6.1"); make("1.10.0"); make("9.9.9", runner=False)
    assert L.find_runner(plugins).parent.parent.name == "1.10.0"                # numeric, not string, order
    (cache / "1.10.0" / ".orphaned_at").write_text("1")
    assert L.find_runner(plugins).parent.parent.name == "1.6.1"                 # orphaned and runner-less skipped
    # installed_plugins.json: the recorded active install wins when it is usable
    (plugins / "installed_plugins.json").write_text(json.dumps({"version": 2, "plugins": {"yolk@yolk": [
        {"scope": "user", "installPath": str(cache / "1.5.0"), "version": "1.5.0"}], "other@x": [{"installPath": "/zzz"}]}}))
    assert L.find_runner(plugins).parent.parent.name == "1.5.0"
    (plugins / "installed_plugins.json").write_text(json.dumps({"plugins": {"yolk@yolk": [
        {"installPath": str(cache / "1.10.0")}]}}))                             # recorded but orphaned: fall back
    assert L.find_runner(plugins).parent.parent.name == "1.6.1"
    (plugins / "installed_plugins.json").write_text("{broken")                  # unreadable: fall back
    assert L.find_runner(plugins).parent.parent.name == "1.6.1"
    # nothing found: one log line in agent.log, exit 0 (no exec)
    home = Path(tempfile.mkdtemp(prefix="yolk-lh-"))
    old = (L.find_runner, os.environ.get("YOLK_HOME"))
    L.find_runner = lambda: None
    os.environ["YOLK_HOME"] = str(home)
    try:
        L.main()
    finally:
        L.find_runner = old[0]
        os.environ["YOLK_HOME"] = old[1]
    lines = (home / "audit" / "agent.log").read_text().strip().splitlines()
    assert len(lines) == 1 and "no installed Yolk plugin" in lines[0]
    # the exec hands the arguments over to the found runner
    out = plugins / "out.txt"
    (cache / "1.6.1" / "scripts" / "employee_runner.py").write_text(
        f"import sys; open({str(out)!r}, 'w').write(' '.join(sys.argv[1:]))")
    import subprocess
    env = dict(os.environ, HOME=str(plugins / "fakehome"))
    (plugins / "fakehome" / ".claude").mkdir(parents=True)
    (plugins / "fakehome" / ".claude" / "plugins").symlink_to(plugins)
    subprocess.run([sys.executable, str(config.SCRIPTS_DIR / "launcher.py"), "summarize", "--limit", "1"], env=env, timeout=30)
    assert out.read_text() == "summarize --limit 1"
