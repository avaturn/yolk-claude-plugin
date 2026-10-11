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


def heal_schedule():
    if os.environ.get("XPC_SERVICE_NAME") == "com.yolk.agent" or os.getppid() == 1:
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
    if points_at_launcher and current and on_calendar:
        return
    _reinstall("The background poller " + (("ran an outdated launcher" if not current else "used the old schedule")
                                           if points_at_launcher else "did not use the launcher"),
               "updated it.", "could not update it (run scripts/install_schedule.sh).")
