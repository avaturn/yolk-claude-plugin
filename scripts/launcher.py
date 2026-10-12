#!/usr/bin/env python3
"""Yolk poller launcher: the one file launchd runs, copied by install_schedule.sh to
YOLK_HOME/bin/launcher.py. Claude Code keeps every plugin version in its own directory and
deletes an old one 14 days after an update, so a plist pinned to a version path dies. This file
never moves: it finds the newest installed copy and hands over to its employee_runner.py.
Stdlib only, Python 3.9."""
import json
import os
import re
import sys
import time
from pathlib import Path

PLUGINS_DIR = Path.home() / ".claude" / "plugins"
# The marketplace was renamed yolk -> avaturn. A device that added it before the rename keeps the local
# name through every update, so it stays yolk@yolk. Cache layout: cache/<marketplace>/<plugin>/<version>/.
MARKETPLACES = ("avaturn", "yolk")


def version_key(path):
    """'1.10.0' > '1.6.1': numeric parts, not strings."""
    return tuple(int(n) for n in re.findall(r"\d+", Path(path).name))


def usable(path):
    p = Path(path)
    return (p / "scripts" / "employee_runner.py").is_file() and not (p / ".orphaned_at").exists()


def recorded_paths(plugins_dir):
    """installPath of every `yolk@<marketplace>` entry in installed_plugins.json (Claude Code's own
    record of the active install); nothing if the file or the entries are missing or unreadable."""
    try:
        plugins = json.loads((plugins_dir / "installed_plugins.json").read_text())["plugins"]
        entries = [e for m in MARKETPLACES for e in plugins.get(f"yolk@{m}", [])]
        return [e["installPath"] for e in entries if isinstance(e, dict) and e.get("installPath")]
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return []


def find_runner(plugins_dir=PLUGINS_DIR):
    """The runner of the active install: the recorded path if usable, else the newest usable
    version directory in the cache. None if there is none."""
    recorded = sorted((p for p in recorded_paths(plugins_dir) if usable(p)), key=version_key, reverse=True)
    if recorded:
        return Path(recorded[0]) / "scripts" / "employee_runner.py"
    found = sorted((d for m in MARKETPLACES for d in (plugins_dir / "cache" / m / "yolk").glob("*/")
                    if d.is_dir() and usable(d)), key=version_key, reverse=True)
    return found[0] / "scripts" / "employee_runner.py" if found else None


def main():
    runner = find_runner()
    if runner is None:
        home = Path(os.environ.get("YOLK_HOME", str(Path.home() / ".claude" / "yolk")))
        try:
            (home / "audit").mkdir(parents=True, exist_ok=True)
            with open(home / "audit" / "agent.log", "a") as f:
                f.write(f"[yolk {time.strftime('%H:%M:%S')}] launcher: no installed Yolk plugin found "
                        f"under {PLUGINS_DIR / 'cache'}; reinstall it with /plugin install yolk@avaturn\n")
        except OSError:
            pass
        return
    os.execv(sys.executable, [sys.executable, str(runner)] + sys.argv[1:])


if __name__ == "__main__":
    main()
