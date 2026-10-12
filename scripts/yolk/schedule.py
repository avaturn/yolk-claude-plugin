"""launchd: install the background poller and keep it current.

The plist runs YOLK_HOME/bin/launcher.py, a copy of scripts/launcher.py that never moves and hands
over to the newest installed plugin version (Claude Code keeps each version in its own directory and
deletes an old one 14 days after an update). So an update reaches the poller by itself; `heal_schedule`
only has to keep the launcher copy and the plist right. It runs from the SessionStart hook and from
every action but `leave` and `status`.

The poller is mandatory, so a MISSING plist is installed too, but only for a linked, installed
plugin on the default YOLK_HOME: never from a dev or test run, never before `claim` wrote the config,
never after `leave` (which deletes agent.json and the plist). install_schedule.sh bakes the caller's
YOLK_HOME into the plist, hence the guards. A run started by launchd never reinstalls: that would
unload (kill) the job it runs in.
"""
import os
import plistlib
import subprocess
from datetime import date

from . import config, store


def install_schedule():
    """Re-run install_schedule.sh; True if it succeeded."""
    try:
        return subprocess.run(["bash", str(config.INSTALL_SCRIPT)], capture_output=True, text=True,
                              timeout=25).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _installed_copy():
    """This is the copy Claude Code installed, not a dev checkout."""
    return config.PLUGIN_ROOT.is_relative_to(config.INSTALLED_PLUGINS_DIR)


def _may_install_missing():
    return (config.CONFIG_FILE.exists() and _installed_copy()
            and config.YOLK_HOME == config.DEFAULT_YOLK_HOME
            and os.environ.get("YOLK_HOME", str(config.DEFAULT_YOLK_HOME)) == str(config.DEFAULT_YOLK_HOME))


def _reinstall(what, done, failed):
    store.log(f"{what}; " + (done if install_schedule() else failed))


def apple_python():
    """Apple's Python.app executable when developer tools have it, else None. Mirrors install_schedule.sh."""
    try:
        dev = subprocess.run(["xcode-select", "-p"], capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return None
    python = os.path.join(dev, config.APPLE_PYTHON_APP)
    return python if dev and os.access(python, os.X_OK) else None


def run_by_launchd():
    """Inside the com.yolk.agent job: started by launchd (directly, or through the launcher's execv), or a
    child that inherits its XPC_SERVICE_NAME, like the poller's own `claude -p` runs."""
    return os.environ.get("XPC_SERVICE_NAME") == "com.yolk.agent" or os.getppid() == 1


def record_full_disk_access():
    """In the job only, record whether it may read the Screen Time database (None: there is none). A run
    from a session would test its app's grant, not the job's."""
    if not run_by_launchd():
        return
    try:
        open(config.SCREEN_TIME_DB, "rb").close()
        fda = True
    except PermissionError:
        fda = False
    except OSError:
        fda = None
    store.set_status(full_disk_access=fda)


def full_disk_access_hint():
    """One line for the session when the job's last poll was denied Screen Time, else None. At most once a
    local day, and never in the job's own runs: their SessionStart hook output would reach answers. It names
    what macOS checks: the program launchd starts, symlinks resolved, or the app bundle it is in."""
    hint_file = config.FDA_HINT_FILE
    if (run_by_launchd() or store.read_status().get("full_disk_access") is not False
            or hint_file.exists() and date.fromtimestamp(hint_file.stat().st_mtime) == date.today()):
        return None
    try:
        path = os.path.realpath(plistlib.loads(config.LAUNCH_PLIST.read_bytes())["ProgramArguments"][0])
    except Exception:  # noqa: BLE001
        return None
    app, sep, _ = path.partition(".app/Contents/MacOS/")
    path = app + ".app" if sep else path
    hint_file.touch()
    return ("Yolk: Screen Time is blocked because the background job has no Full Disk Access. Tell the user: "
            f'System Settings > Privacy & Security > Full Disk Access > "+" > Cmd+Shift+G > {path}, then turn it on.')


def heal_schedule():
    if run_by_launchd():
        return
    if not config.LAUNCH_PLIST.exists():
        if _may_install_missing():
            _reinstall("The background poller was not installed", "installed it.",
                       "could not install it (run scripts/install_schedule.sh).")
        return
    try:
        plist = plistlib.loads(config.LAUNCH_PLIST.read_bytes())
    except Exception:  # noqa: BLE001
        store.log("background poller plist unreadable: run install_schedule.sh (or /yolk:setup)", echo=False)  # a session hook must stay silent
        return
    if (plist.get("EnvironmentVariables") or {}).get("YOLK_HOME") != str(config.YOLK_HOME) or not _installed_copy():
        return
    installed = config.YOLK_HOME / "bin" / "launcher.py"
    points_at_launcher = any(isinstance(a, str) and os.path.realpath(a) == os.path.realpath(installed)
                             for a in plist.get("ProgramArguments") or [])
    try:
        current = installed.read_bytes() == (config.SCRIPTS_DIR / "launcher.py").read_bytes()
    except OSError:
        current = False
    on_calendar = "StartCalendarInterval" in plist and "StartInterval" not in plist  # StartInterval misses firings during sleep
    if not points_at_launcher:
        reason = "did not use the launcher"
    elif not current:
        reason = "ran an outdated launcher"
    elif not on_calendar:
        reason = "used the old schedule"
    elif plist["StartCalendarInterval"] != [{"Minute": m} for m in range(0, 60, config.POLL_INTERVAL_S // 60)]:
        reason = "used an old interval"
    elif not os.path.exists(python := plist["ProgramArguments"][0]):
        reason = "ran a python3 that no longer exists"
    elif python.endswith("/" + config.APPLE_PYTHON_APP) or apple_python() is None:  # xcode-select last: the only subprocess
        return  # on an Apple Python.app already (any developer dir: no flip-flop), or there is none to move to
    else:
        reason = "did not run Apple's Python.app, whose Full Disk Access lasts"
    _reinstall(f"The background poller {reason}", "updated it.", "could not update it (run scripts/install_schedule.sh).")
