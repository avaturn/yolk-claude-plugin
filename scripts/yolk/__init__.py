"""Yolk employee agent: the device side of Yolk Device API v1.

Runs on the employee's device. It answers the server's daily check-in with sanitized text and builds
the employee's own daily activity report locally; raw activity data and reports never leave the
device. No manager logic and no API keys live here.

Modules, from the bottom up (no module imports one below it in this list):
  config    paths, limits, the clock, agent.json and profile
  store     private atomic writes, agent.log, status.json, day files, outbox, pruning
  api       the Device API gateway: the only HTTP code
  claude    the only code that starts `claude -p`
  privacy   the privacy gate and the shape of what is stored or sent
  activity  the daily activity report: collect, summarize, store
  checkin   answer one check-in
  schedule  launchd install and self-heal
  poll      one poll cycle, in its fixed order
  status    what is happening now
  cli       the actions and their top-level errors

Developing:
  python3 tests/check.py             all checks (unit, one temp YOLK_HOME)
  python3 tests/check_baseline.py    one full poll against a stub API, compared with tests/baseline.json
  YOLK_SERVER=http://localhost:8000  point a local checkout at a local server (plain HTTP only for loopback)
  Claude Code runs a cached copy (~/.claude/plugins/cache/yolk/yolk/<version>/); after editing a checkout
  bump the version in .claude-plugin/plugin.json and reinstall: `claude plugin uninstall yolk@yolk &&
  claude plugin install yolk@yolk`.

Modules call each other as `module.function(...)`, never `from module import function`, so a test
can patch one name in one place.
"""
import json
from pathlib import Path

VERSION = json.loads((Path(__file__).resolve().parents[2] / ".claude-plugin" / "plugin.json").read_text())["version"]
