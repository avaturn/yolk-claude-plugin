"""Paths, limits, the device clock, agent.json and the profile.

Everything here is read once, at import: YOLK_HOME and YOLK_SERVER come from the environment (a test
or a local server sets them before the first import), the rest is fixed.

Data kept under YOLK_HOME (default ~/.claude/yolk):
  agent.json              server, device_token, employee_id, timezone, poll_interval_s (0600)
  profile.md              the profile the manager registered
  status.json             progress of the poll: phases, dates, check-in dates; never content
  briefs/<date>.json      coach briefs (reports/ holds older daily reports, no longer written)
  days/<date>.json        raw collector output of one day (0600, local only)
  days/<date>.summary.json  the daily activity report (0600, local only)
  days/<date>.attempts    failed builds of that day so far; .cooldown: claude failed, wait
  outbox/<set id>.json    answers saved but not yet accepted by the server
  audit/                  agent.log, <set id>.raw.json (before the privacy gate), <set id>.sent.json,
                          summary-<date>.failed.json (a day given up on)
  bin/launcher.py         what launchd runs (copied by install_schedule.sh)

The clock: the server judges dates in the employee's timezone, the collectors cut days in the
machine's. A day counts as finished only when it is over in both (`summary_today`).
"""
import json
import os
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
PLUGIN_ROOT = SCRIPTS_DIR.parent
# the server invite codes are claimed on; YOLK_SERVER overrides it (local checks)
DEFAULT_SERVER = os.environ.get("YOLK_SERVER", "https://plugin.dev.yolk.coach").rstrip("/")
YOLK_HOME = Path(os.environ.get("YOLK_HOME", str(Path.home() / ".claude" / "yolk")))
CONFIG_FILE = YOLK_HOME / "agent.json"
PROFILE_FILE = YOLK_HOME / "profile.md"
STATUS_FILE = YOLK_HOME / "status.json"
BRIEFS_DIR = YOLK_HOME / "briefs"
REPORTS_DIR = YOLK_HOME / "reports"  # legacy: only removed when empty
OUTBOX_DIR = YOLK_HOME / "outbox"
DAYS_DIR = YOLK_HOME / "days"
AUDIT_DIR = YOLK_HOME / "audit"
# not in agents/: files there register as `yolk:*` subagents in every Claude Code session
CHARTER = PLUGIN_ROOT / "charters" / "employee-investigator.md"
SUMMARIZER_CHARTER = PLUGIN_ROOT / "charters" / "day-summarizer.md"
COLLECTORS_DIR = SCRIPTS_DIR / "collectors"
INSTALL_SCRIPT = SCRIPTS_DIR / "install_schedule.sh"
LAUNCH_PLIST = Path.home() / "Library" / "LaunchAgents" / "com.yolk.agent.plist"
DEFAULT_YOLK_HOME = Path.home() / ".claude" / "yolk"
INSTALLED_PLUGINS_DIR = (Path.home() / ".claude" / "plugins").resolve()

CLAUDE_MODEL = "claude-opus-5-5"
CLAUDE_EFFORT = "high"
CLAUDE_TIMEOUT_S = 1500
SUMMARY_TIMEOUT_S = 600     # the summarizer only reads one JSON blob and answers
COLLECTOR_TIMEOUT_S = 120
SUMMARY_BACKFILL_DAYS = 7   # finished days from today-7 to yesterday are summarized
BACKFILL_PER_POLL = 2       # after the answers; a question date's own summary is not limited
SUMMARY_MAX_ATTEMPTS = 3    # failed builds of one day before it is given up
COOLDOWN_S = 6 * 3600       # a day whose claude run exited non-zero is left alone this long
RAW_KEEP_DAYS = 14          # days/* files older than this are deleted
ANSWERED_KEEP_S = 6 * 3600  # a check-in answered this recently is still awaiting its brief
WAIT_POLL_S = 5             # status --wait: check for a running poll this often
WAIT_SYNC_S = 60            # ...and sync briefs this often while a check-in is open

DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
SET_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")  # server ids become local file names
LOOPBACK = ("localhost", "127.0.0.1", "::1")


def machine_timezone():
    try:
        tz = os.readlink("/etc/localtime")
        return tz.split("zoneinfo/")[-1]
    except OSError:
        return datetime.now().astimezone().tzname() or "UTC"


def employee_now(cfg):
    """Now in the employee's timezone; the machine's if that is unknown."""
    try:
        return datetime.now(ZoneInfo(cfg["timezone"]))
    except (KeyError, ValueError, TypeError, OSError):  # ZoneInfoNotFoundError is a KeyError
        return datetime.now().astimezone()


def summary_today(cfg):
    """The first day that has NOT ended yet: the earlier of today in the employee's timezone
    and today on this machine."""
    return min(employee_now(cfg).date(), datetime.now().date())


def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def parse_date(s):
    try:
        return datetime.strptime(s, "%Y-%m-%d").date() if DATE_RE.fullmatch(s) else None
    except (TypeError, ValueError):
        return None


def load_config():
    if not CONFIG_FILE.exists():
        raise SystemExit("This Mac is not linked to Yolk. Run /yolk:setup <invite code>.")
    return json.loads(CONFIG_FILE.read_text())


def load_profile():
    if PROFILE_FILE.exists():
        return PROFILE_FILE.read_text().strip()
    return "No profile on file (role unknown — note this in answers)."
