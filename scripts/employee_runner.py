#!/usr/bin/env python3
"""Yolk — Employee Agent runner (the device side of Yolk Device API v1).

Runs entirely on the employee's Mac. Every poll it builds a sanitized daily summary
of each past workday that has none yet (collectors run here, one tool-less headless
Claude Code run turns the raw data into what/where/how blocks) and posts ONLY that
summary. It also polls for the Manager AI's question sets, investigates locally
(headless Claude Code with full local freedom), sanitizes the answers, and posts ONLY
the sanitized answers. Raw collector output never leaves the Mac. Also fetches the
employee's own daily reports + coach briefs and sends a heartbeat.
No manager logic and no API keys live on this side.

Data (YOLK_HOME, default ~/.claude/yolk/):
  agent.json            server, device_token, employee_id, timezone, poll_interval_s (0600)
  profile.md            the profile the manager registered (from the claim)
  briefs/<date>.json    coach briefs        reports/<date>.md   daily reports
  days/<date>.json      raw collector output of one day (0600, never sent, deleted after 14 days)
  days/<date>.summary.json  the sanitized summary built for that day (what is sent)
  days/<date>.attempts  failed builds of that day so far
  outbox/<set id>.json  answers saved but not yet accepted by the server
  outbox/summaries/<date>.json  daily summaries saved but not yet accepted
  audit/                agent.log, <set id>.raw.json (before the gate), <set id>.sent.json,
                        summary-<date>.sent.json / .rejected.json / .failed.json (given up)

Usage: employee_runner.py [poll] [--verbose]    one poll cycle (what launchd runs)
       employee_runner.py claim <invite code>   link this Mac (/yolk:setup)
       employee_runner.py summarize [--date D]  build + send the daily summary (all missing days, or D)
       employee_runner.py sync                  fetch briefs + heartbeat
       employee_runner.py leave                 revoke this Mac's device token
"""
import argparse
import fcntl
import json
import os
import platform
import plistlib
import re
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from sanitizer import sanitize_answers  # noqa: E402

PLUGIN_ROOT = HERE.parent
VERSION = json.loads((PLUGIN_ROOT / ".claude-plugin" / "plugin.json").read_text())["version"]
# the server invite codes are claimed on; YOLK_SERVER overrides it (local checks)
DEFAULT_SERVER = os.environ.get("YOLK_SERVER", "https://plugin.dev.yolk.coach").rstrip("/")
YOLK_HOME = Path(os.environ.get("YOLK_HOME", str(Path.home() / ".claude" / "yolk")))
CONFIG_FILE = YOLK_HOME / "agent.json"
PROFILE_FILE = YOLK_HOME / "profile.md"
BRIEFS_DIR = YOLK_HOME / "briefs"
REPORTS_DIR = YOLK_HOME / "reports"
OUTBOX_DIR = YOLK_HOME / "outbox"
SUMMARY_OUTBOX_DIR = OUTBOX_DIR / "summaries"  # apart from outbox/*.json, which flush_outbox globs
DAYS_DIR = YOLK_HOME / "days"
AUDIT_DIR = YOLK_HOME / "audit"
# not in agents/: files there register as `yolk:*` subagents in every Claude Code session
CHARTER = PLUGIN_ROOT / "charters" / "employee-investigator.md"
SUMMARIZER_CHARTER = PLUGIN_ROOT / "charters" / "day-summarizer.md"
LAUNCH_PLIST = Path.home() / "Library" / "LaunchAgents" / "com.yolk.agent.plist"
INSTALL_SCRIPT = HERE / "install_schedule.sh"
COLLECTORS_DIR = HERE / "collectors"
CLAUDE_TIMEOUT_S = 1500
SUMMARY_TIMEOUT_S = 600     # the summarizer only reads one JSON blob and answers
COLLECTOR_TIMEOUT_S = 120
SUMMARY_BACKFILL_DAYS = 7
SUMMARY_MAX_AGE_DAYS = 14   # the server rejects older dates (422)
SUMMARY_MAX_ATTEMPTS = 3    # failed builds of one day before it is given up
RAW_KEEP_DAYS = 14          # days/* files older than this are deleted
# source name in the contract -> collector script
COLLECTORS = {"claude_sessions": "claude_sessions.py", "screen_time": "screen_time.py",
              "git": "git_activity.py", "browser": "browser_history.py"}
BLOCK_KINDS = ("work", "meeting", "personal", "away")
CONFIDENCES = ("high", "medium", "low")
MAX_BLOCKS, MAX_WORK_ITEMS, OVERVIEW_MAX = 48, 20, 1500
BLOCK_LIMITS = {"what": 300, "where": 200, "how": 300}
ITEM_LIMITS = {"title": 200, "outcome": 500, "evidence": 300}
HHMM_RE = re.compile(r"(\d{1,2}):(\d{2})")
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
SET_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")  # server ids become local file names
LOOPBACK = ("localhost", "127.0.0.1", "::1")
ANSWER_ITEM_KEYS = ("question_id", "answer", "evidence", "confidence")


class Unlinked(Exception):
    """The server answered 401: this Mac's device token is no longer valid."""


def log(msg):
    line = f"[yolk {datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    with open(AUDIT_DIR / "agent.log", "a") as f:
        f.write(line + "\n")


def machine_timezone():
    try:
        tz = os.readlink("/etc/localtime")
        return tz.split("zoneinfo/")[-1]
    except OSError:
        return datetime.now().astimezone().tzname() or "UTC"


def employee_now(cfg):
    """Now in the employee's timezone (the server judges dates there); the machine's if unknown."""
    try:
        return datetime.now(ZoneInfo(cfg["timezone"]))
    except (KeyError, ValueError, TypeError, OSError):  # ZoneInfoNotFoundError is a KeyError
        return datetime.now().astimezone()


def parse_date(s):
    try:
        return datetime.strptime(s, "%Y-%m-%d").date() if DATE_RE.fullmatch(s) else None
    except (TypeError, ValueError):
        return None


def ensure_dirs():
    for d in (YOLK_HOME, BRIEFS_DIR, REPORTS_DIR, OUTBOX_DIR, SUMMARY_OUTBOX_DIR, AUDIT_DIR, DAYS_DIR):
        d.mkdir(parents=True, exist_ok=True)
    os.chmod(DAYS_DIR, 0o700)  # raw collector output: prompt previews, commands, page titles


def summary_today(cfg):
    """The first day that has NOT ended yet: the earlier of today in the employee's timezone
    and today on this machine, so a day is never summarized before it is over here."""
    return min(employee_now(cfg).date(), datetime.now().date())


def write_private(path, text):
    """Atomic (temp file in the same dir, then os.replace) and 0600: a crash or a full disk
    never leaves a half-written outbox body or device config."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")  # mkstemp: 0600
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def load_config():
    if not CONFIG_FILE.exists():
        raise SystemExit("This Mac is not linked to Yolk. Run /yolk:setup <invite code>.")
    return json.loads(CONFIG_FILE.read_text())


def load_profile():
    if PROFILE_FILE.exists():
        return PROFILE_FILE.read_text().strip()
    return "No profile on file (role unknown — note this in answers)."


def api(cfg, method, path, body=None, auth=True):
    """One Device API call. Returns (status, parsed JSON or None); raises Unlinked on 401."""
    headers = {"Content-Type": "application/json"}
    if auth:
        headers["Authorization"] = f"Bearer {cfg['device_token']}"
    req = urllib.request.Request(cfg["server"] + path, method=method, headers=headers,
                                 data=None if body is None else json.dumps(body).encode())
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        if e.code == 401 and auth:
            raise Unlinked() from None
        return e.code, None


def claim(code):
    code = code.strip()
    if not re.fullmatch(r"[A-Za-z0-9 -]+", code):
        raise SystemExit("Give the invite code from your manager (like K7QP-3FXA), not a link.")
    server = DEFAULT_SERVER
    u = urllib.parse.urlparse(server)
    if u.scheme != "https" and u.hostname not in LOOPBACK:
        raise SystemExit("Refusing a non-HTTPS server: the device token must not travel unencrypted.")
    device = {"hostname": socket.gethostname().removesuffix(".local"),
              "os": f"macOS {platform.mac_ver()[0]}",
              "timezone": machine_timezone(), "agent_version": VERSION}
    status, body = api({"server": server}, "POST", "/v1/devices/claim",
                       {"invite_code": code, "device": device}, auth=False)
    if status == 404:
        raise SystemExit("This invite isn't valid. Check the code with your manager.")
    if status == 410:
        raise SystemExit("This invite was already used or expired. Ask your manager for a new one.")
    if status != 200:
        raise SystemExit(f"Linking failed (HTTP {status}).")
    emp = body["employee"]
    cfg = {"server": server, "device_token": body["device_token"], "employee_id": emp["id"],
           "name": emp.get("name"), "timezone": emp.get("timezone") or machine_timezone(),
           "poll_interval_s": body.get("poll_interval_s", 1200)}
    cfg["linked_on"] = summary_today(cfg).isoformat()
    write_private(CONFIG_FILE, json.dumps(cfg, indent=1) + "\n")
    PROFILE_FILE.write_text((emp.get("profile_md") or "").strip() + "\n")
    log(f"Linked to {server} as {emp.get('name')} ({emp['id']}), timezone {cfg['timezone']}.")
    sync_briefs(cfg)
    heartbeat(cfg)


def latest_brief():
    return max((p.stem for p in BRIEFS_DIR.glob("*.json") if DATE_RE.fullmatch(p.stem)), default=None)


def sync_briefs(cfg):
    latest = latest_brief()
    _, body = api(cfg, "GET", "/v1/briefs" + (f"?since={latest}" if latest else ""))
    got = 0
    for b in (body or {}).get("briefs", []):
        if not DATE_RE.fullmatch(str(b.get("date"))):
            continue
        (BRIEFS_DIR / f"{b['date']}.json").write_text(
            json.dumps(b.get("brief"), indent=1, ensure_ascii=False) + "\n")
        (REPORTS_DIR / f"{b['date']}.md").write_text((b.get("report_markdown") or "") + "\n")
        got += 1
    if got:
        log(f"Received {got} brief(s) and report(s).")


def heartbeat(cfg, status="ok", error=None):
    sent = max((p for p in AUDIT_DIR.glob("*.sent.json") if not p.name.startswith("summary-")),
               key=lambda p: p.stat().st_mtime, default=None)  # last ANSWER, not last summary
    api(cfg, "POST", "/v1/devices/heartbeat", {
        "agent_version": VERSION, "status": status, "error": error,
        "last_answered_at": datetime.fromtimestamp(sent.stat().st_mtime).astimezone()
                                    .isoformat(timespec="seconds") if sent else None,
        "latest_brief": latest_brief()})


def load_outbox(f, rejected_name):
    """The body of an outbox file. An unreadable one is moved to audit/<name>.rejected.json
    and None is returned: one bad file must not abort the poll."""
    try:
        body = json.loads(f.read_text())
        if isinstance(body, dict):
            return body
    except (OSError, ValueError):  # UnicodeDecodeError and JSONDecodeError are ValueErrors
        pass
    f.replace(AUDIT_DIR / f"{rejected_name}.rejected.json")
    log(f"Outbox file {f.name} is unreadable; moved to audit/{rejected_name}.rejected.json.")
    return None


def flush_outbox(cfg):
    """Post every saved answer. 409 means the server already has it — also success."""
    for f in sorted(OUTBOX_DIR.glob("*.json")):
        body = load_outbox(f, f.stem)
        if body is None:
            continue
        status, _ = api(cfg, "POST", f"/v1/questions/{f.stem}/answers", body)
        if status in (201, 409):
            f.replace(AUDIT_DIR / f"{f.stem}.sent.json")
            log(f"Sent answers for {f.stem}" + (" (server already had them)" if status == 409 else ""))
        elif status in (400, 404, 422):  # retrying the same body cannot succeed
            f.replace(AUDIT_DIR / f"{f.stem}.rejected.json")
            log(f"Server rejected {f.stem} (HTTP {status}); kept in audit/{f.stem}.rejected.json.")
        else:
            log(f"Could not send {f.stem} (HTTP {status}); retrying next poll.")


def flush_summaries(cfg):
    """Post every saved daily summary. 409 = the server has it (success); 422 = never resend."""
    for f in sorted(SUMMARY_OUTBOX_DIR.glob("*.json")):
        if not DATE_RE.fullmatch(f.stem):
            continue
        body = load_outbox(f, f"summary-{f.stem}")
        if body is None:
            continue
        status, _ = api(cfg, "POST", f"/v1/summaries/{f.stem}", body)
        sent = AUDIT_DIR / f"summary-{f.stem}.sent.json"
        if status == 409 and sent.exists():
            f.unlink()  # the server kept its FIRST copy, and so does the audit trail
            log(f"The server already had the summary for {f.stem}.")
        elif status in (201, 409):
            f.replace(sent)
            log(f"Sent the summary for {f.stem}" + (" (server already had it)" if status == 409 else ""))
        elif status == 422:
            f.replace(AUDIT_DIR / f"summary-{f.stem}.rejected.json")
            log(f"Server rejected the summary for {f.stem} (HTTP 422); kept in "
                f"audit/summary-{f.stem}.rejected.json, it will not be rebuilt.")
        else:
            log(f"Could not send the summary for {f.stem} (HTTP {status}); retrying next poll.")


def has_activity(v):
    """Any non-zero number, or any element in a list of scalars, anywhere below `date`.
    ponytail: shape-agnostic on purpose (collectors change); strings alone do not count."""
    if isinstance(v, dict):
        return any(has_activity(x) for k, x in v.items() if k != "date")
    if isinstance(v, list):
        return any(has_activity(x) if isinstance(x, (dict, list)) else bool(x) for x in v)
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v != 0


def run_collector(script, date):
    """One collector, run directly (not through the model). Returns (ok|empty|error, data)."""
    try:
        p = subprocess.run([sys.executable, str(script), "--date", date], capture_output=True,
                           text=True, timeout=COLLECTOR_TIMEOUT_S, cwd=PLUGIN_ROOT)
    except subprocess.TimeoutExpired:
        return "error", {"date": date, "error": "timeout"}
    if p.returncode != 0:
        return "error", {"date": date, "error": f"exit_{p.returncode}"}
    try:
        data = json.loads(p.stdout)
    except ValueError:
        return "error", {"date": date, "error": "bad_json"}
    if not isinstance(data, dict) or data.get("error"):
        return "error", data if isinstance(data, dict) else {"date": date, "error": "bad_json"}
    return ("ok" if has_activity(data) else "empty"), data


def collect_day(date, tz):
    """Run the four collectors; the raw result stays in days/<date>.json (0600)."""
    sources, data = {}, {}
    for name, script in COLLECTORS.items():
        sources[name], data[name] = run_collector(COLLECTORS_DIR / script, date)
    raw = {"date": date, "timezone": tz, "sources": sources, "data": data}
    write_private(DAYS_DIR / f"{date}.json", json.dumps(raw, indent=1, ensure_ascii=False))
    return raw


def _text(v, limit):
    return "" if v is None or isinstance(v, (dict, list)) else str(v).strip()[:limit]


def _minutes(v):
    if isinstance(v, bool):
        return 0
    try:
        return max(0, min(int(round(float(v))), 1440))
    except (TypeError, ValueError, OverflowError):
        return 0


def _hhmm(v):
    m = HHMM_RE.fullmatch(str(v).strip())
    return f"{int(m[1]):02d}:{m[2]}" if m and int(m[1]) <= 23 and int(m[2]) <= 59 else None


def _block(b):
    if not isinstance(b, dict):
        return None
    kind = str(b.get("kind", "")).strip().lower()
    start, end = _hhmm(b.get("start")), _hhmm(b.get("end"))
    if kind not in BLOCK_KINDS or not start or not end or start >= end:
        return None
    confidence = str(b.get("confidence", "")).strip().lower()
    out = {"start": start, "end": end, "kind": kind,
           "confidence": confidence if confidence in CONFIDENCES else "low"}
    if kind in ("personal", "away"):  # personal time only as a total; "away" must not smuggle it in
        return {**out, "what": kind, "where": "", "how": ""}
    out.update({k: _text(b.get(k), n) for k, n in BLOCK_LIMITS.items()})
    return out if out["what"] else None


def to_summary_contract(raw):
    """The server refuses unknown keys (422) and over-long strings: keep exactly the
    contract keys, truncate, drop extra/invalid items, coerce enums."""
    s = raw.get("summary") if isinstance(raw, dict) and isinstance(raw.get("summary"), dict) else raw
    s = s if isinstance(s, dict) else {}
    blocks = s.get("blocks") if isinstance(s.get("blocks"), list) else []
    items = s.get("work_items") if isinstance(s.get("work_items"), list) else []
    work_items = [{k: _text(i.get(k), n) for k, n in ITEM_LIMITS.items()}
                  for i in items if isinstance(i, dict) and _text(i.get("title"), 1)]
    return {"overview": _text(s.get("overview"), OVERVIEW_MAX),
            "active_minutes": _minutes(s.get("active_minutes")),
            "personal_minutes": _minutes(s.get("personal_minutes")),
            "blocks": sorted(filter(None, map(_block, blocks)),
                             key=lambda b: (b["start"], b["end"]))[:MAX_BLOCKS],
            "work_items": work_items[:MAX_WORK_ITEMS]}


def build_summary(raw):
    """Privacy gate on the model's full text, THEN the contract shape. The other way round,
    cutting a string to its limit can leave the first characters of a secret that are too
    short for the gate to recognise, and a redaction marker longer than its text could push
    a string over its limit (a permanent 422)."""
    clean, violations = sanitize_answers(raw)
    return to_summary_contract(clean), violations


def missing_days(cfg, today):
    """Past days (newest first) still to summarize, in the employee's timezone: from
    max(the day before linking, today-7) to yesterday; yesterday only without `linked_on`.
    A day is done once its summary is in the outbox, sent, rejected, or given up on.
    (The day before linking, not linking day itself: a Mac linked today must send yesterday now.)"""
    yesterday = today - timedelta(days=1)
    linked = parse_date(str(cfg.get("linked_on")))
    first = max(linked - timedelta(days=1), today - timedelta(days=SUMMARY_BACKFILL_DAYS)) \
        if linked else yesterday
    days = [(yesterday - timedelta(days=i)).isoformat() for i in range((yesterday - first).days + 1)]
    return [d for d in days
            if not (SUMMARY_OUTBOX_DIR / f"{d}.json").exists()
            and not any((AUDIT_DIR / f"summary-{d}.{s}.json").exists() for s in ("sent", "rejected", "failed"))]


def summary_prompt(date, profile, tz, raw):
    return f"""{SUMMARIZER_CHARTER.read_text()}

## Today's assignment
Day to summarize: {date}
Person you represent (calibrate all judgments to this role):
{profile}

TIMEZONE RULE (mandatory): the employee's timezone is **{tz}**, and the machine's
timezone is **{machine_timezone()}**. The collectors cut the day and print times in the
machine's timezone. If the two differ, convert EVERY time to {tz} and state all block
times in {tz}.

Yolk's own headless runs happen in the plugin directory {PLUGIN_ROOT}: Claude sessions
whose project path is that directory are the Yolk agent itself, not the person's work.

## Raw collector data for {date} (local only — never repeat it verbatim)
{json.dumps(raw, ensure_ascii=False)}

Summarize now, then output ONLY the summary JSON object."""


def summarize_day(cfg, date, profile, verbose):
    """Collect, summarize (one tool-less Claude run), sanitize, and queue in the outbox."""
    tz = cfg["timezone"]
    raw = collect_day(date, tz)
    statuses = raw["sources"]
    if "ok" in statuses.values():
        result = run_claude(summary_prompt(date, profile, tz, raw), str(uuid.uuid4()), verbose,
                            "Summarization", toolless=True, max_turns=3, timeout=SUMMARY_TIMEOUT_S)
        try:
            parsed = extract_json(result)
        except ValueError:  # json.JSONDecodeError; its message holds positions only
            parsed = None
        if parsed is None:
            raise BadReply("no JSON in the summarization result")
    else:  # nothing readable to summarize: no model run, and nothing to hallucinate from
        parsed = {"overview": "Local activity data was not available for this day."
                  if "error" in statuses.values() else "No activity was recorded on this device."}
    summary, violations = build_summary(parsed)
    envelope = {"timezone": tz,
                "generated_at": employee_now(cfg).isoformat(timespec="seconds"),
                "agent_version": VERSION, "privacy_redactions": violations,
                "sources": statuses, "summary": summary}
    text = json.dumps(envelope, indent=1, ensure_ascii=False) + "\n"
    write_private(DAYS_DIR / f"{date}.summary.json", text)
    write_private(SUMMARY_OUTBOX_DIR / f"{date}.json", text)
    (DAYS_DIR / f"{date}.attempts").unlink(missing_ok=True)
    log(f"Summarized {date}: " + " ".join(f"{k}={v}" for k, v in statuses.items())
        + f"; {len(summary['blocks'])} block(s), {len(summary['work_items'])} work item(s)"
        + (f" (privacy gate redacted: {violations})" if violations else ""))


def count_failure(date, e):
    """Count one failed build of `date`; the third gives the day up (labels only, no content)."""
    f = DAYS_DIR / f"{date}.attempts"
    try:
        n = int(f.read_text()) + 1
    except (OSError, ValueError):
        n = 1
    write_private(f, str(n))
    if n >= SUMMARY_MAX_ATTEMPTS:
        write_private(AUDIT_DIR / f"summary-{date}.failed.json", json.dumps(
            {"date": date, "attempts": n, "last_error": e.__class__.__name__,
             "failed_at": datetime.now().astimezone().isoformat(timespec="seconds")}, indent=1) + "\n")
        log(f"Giving up on the summary for {date} after {n} failed attempts "
            f"(audit/summary-{date}.failed.json); the Manager AI will ask questions instead.")
    return n


def summarize_days(cfg, days, profile, verbose):
    """Build and send one summary per day. Returns (built, error).
    claude missing: stop, nothing to count. claude ran and failed (exit, timeout): count it and
    stop (it would fail again, up to 10 minutes per remaining day under .lock). A bad reply or
    any other failure: count it and go on to the next day."""
    built, error = 0, None
    for d in days:
        try:
            summarize_day(cfg, d, profile, verbose)
        except ClaudeUnavailable as e:
            log(f"Summary for {d} skipped: {e}")
            return built, "summary failed (ClaudeUnavailable)"
        except Exception as e:  # noqa: BLE001 — one bad day must not block the rest
            n = count_failure(d, e)
            log(f"Summary for {d} failed (attempt {n}/{SUMMARY_MAX_ATTEMPTS}). ({e.__class__.__name__}: {e})")
            error = f"summary failed ({e.__class__.__name__})"
            if isinstance(e, ClaudeFailed):
                return built, error
            continue
        built += 1
        flush_summaries(cfg)
    return built, error


def summarize_cli(cfg, date, verbose):
    today = summary_today(cfg)
    if date:  # an explicit date is rebuilt even if one was already sent
        d = parse_date(date)
        if not d or not today - timedelta(days=SUMMARY_MAX_AGE_DAYS) <= d < today:
            raise SystemExit(f"--date must be a past day (before {today}) within the last "
                             f"{SUMMARY_MAX_AGE_DAYS} days; the server rejects anything else.")
        days = [date]
    else:
        days = missing_days(cfg, today)
    flush_summaries(cfg)
    built, error = summarize_days(cfg, days, load_profile(), verbose)
    log(f"Done — {built} summary(ies) built.")
    if error:
        raise SystemExit(error)


def prune_days(cfg):
    """Delete days/* files whose date is older than RAW_KEEP_DAYS: raw collector output must
    not pile up on disk (the server rejects such days anyway). Names without a date stay."""
    cutoff = summary_today(cfg) - timedelta(days=RAW_KEEP_DAYS)
    gone = 0
    for f in DAYS_DIR.glob("*"):
        d = parse_date(f.name[:10])
        if d and d < cutoff and f.is_file():
            f.unlink()
            gone += 1
    if gone:
        log(f"Removed {gone} raw day file(s) older than {RAW_KEEP_DAYS} days.")


def install_schedule():
    """Re-run install_schedule.sh; True if it succeeded."""
    try:
        return subprocess.run(["bash", str(INSTALL_SCRIPT)], capture_output=True, text=True,
                              timeout=60).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def heal_schedule():
    """The poller's launchd plist pins THIS plugin copy's path (.../cache/yolk/yolk/<version>/),
    so after an update it keeps running the old copy, or breaks once Claude Code deletes it.
    When any run finds the plist pointing elsewhere, re-install it. No plist = the user
    declined the poller: never create one."""
    if not LAUNCH_PLIST.exists():
        return
    if os.environ.get("XPC_SERVICE_NAME") == "com.yolk.agent" or os.getppid() == 1:
        return  # launchd started this run, so the plist is current; reinstalling would unload (kill) it
    try:
        args = plistlib.loads(LAUNCH_PLIST.read_bytes()).get("ProgramArguments") or []
    except Exception:  # noqa: BLE001 — an unreadable plist is a stale plist
        args = []
    me = os.path.realpath(HERE / "employee_runner.py")  # realpath: the cache dir may sit behind a symlink
    if any(isinstance(a, str) and os.path.realpath(a) == me for a in args):
        return
    log("The background poller pointed at another plugin copy; "
        + ("re-pointed it at this one." if install_schedule()
           else "could not re-point it (run scripts/install_schedule.sh)."))


def to_contract(raw):
    """The server refuses unknown keys (422): keep only what Device API v1 accepts."""
    items = []
    for a in raw.get("answers") or []:
        if not isinstance(a, dict) or a.get("question_id") is None:
            continue
        item = {k: str(a[k]) for k in ANSWER_ITEM_KEYS if a.get(k) is not None}
        if "evidence" not in item and a.get("evidence_basis") is not None:
            item["evidence"] = str(a["evidence_basis"])  # the charter's old name; never send both
        item["answer"] = item.get("answer", "")
        confidence = item.get("confidence", "").lower()
        item["confidence"] = confidence if confidence in ("high", "medium", "low") else None
        items.append(item)
    context = raw.get("volunteered_context")
    return {"answers": items, "volunteered_context": str(context) if context else None}


class ClaudeUnavailable(RuntimeError):
    """`claude` cannot be started (not installed, not on PATH)."""


class ClaudeFailed(RuntimeError):
    """`claude` ran and failed: non-zero exit, timeout, or no result."""


class BadReply(RuntimeError):
    """`claude` answered, but not with usable JSON."""


def extract_json(text):
    m = re.search(r"\{[\s\S]*\}", text)
    return json.loads(m.group(0)) if m else None


def run_claude(prompt, session_id, verbose, what, toolless=False, max_turns=60, timeout=CLAUDE_TIMEOUT_S):
    """One headless Claude Code run; returns its final result text, raises ClaudeUnavailable
    or ClaudeFailed (both RuntimeErrors).

    toolless: the prompt goes in over stdin to a run with NO tools — it can only answer,
    so text inside the data it reads (page titles, prompts) cannot make it act.
    `--tools ""` alone leaves the user's MCP connectors (Gmail, Slack, ...) loaded;
    `--strict-mcp-config` with no `--mcp-config` drops them (checked: 0 tools in the init event)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE")}
    env["ANTHROPIC_API_KEY"] = ""  # local subscription auth only
    cmd = ["claude", "-p"] + ([] if toolless else [prompt]) \
        + ["--output-format", "stream-json", "--verbose", "--no-session-persistence"] \
        + (["--tools", "", "--strict-mcp-config"] if toolless else ["--dangerously-skip-permissions"]) \
        + ["--session-id", session_id, "--max-turns", str(max_turns)]
    result_text = None
    with tempfile.TemporaryFile("w+") as err, \
            tempfile.TemporaryFile("w+", encoding="utf-8") as stdin:  # files, not pipes: nothing can block claude
        if toolless:
            stdin.write(prompt)
            stdin.flush()
            stdin.seek(0)
        # own process group: children that inherited stdout must die too, or the read never ends
        try:
            proc = subprocess.Popen(cmd, cwd=PLUGIN_ROOT, env=env, text=True, start_new_session=True,
                                    stdin=stdin if toolless else None, stdout=subprocess.PIPE, stderr=err)
        except OSError as e:
            raise ClaudeUnavailable(f"{what}: cannot start claude ({e.__class__.__name__})") from None

        def kill_group():
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

        timer = threading.Timer(timeout, kill_group)  # a hung claude must not keep .lock
        timer.start()
        try:
            for line in proc.stdout:
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if ev.get("type") == "assistant" and verbose:
                    for b in (ev.get("message") or {}).get("content") or []:
                        if b.get("type") == "tool_use":
                            log(f"  · {b.get('name')} {json.dumps(b.get('input') or {})[:90]}")
                elif ev.get("type") == "result":
                    result_text = ev.get("result", "")
            proc.wait()
        finally:
            timer.cancel()
            kill_group()  # leftover children of a finished or interrupted run
        if proc.returncode != 0 or result_text is None:
            err.seek(0)
            reason = "timed out" if proc.returncode == -signal.SIGKILL else f"rc={proc.returncode}"
            raise ClaudeFailed(f"{what} failed ({reason}): {err.read()[-400:]}")
    return result_text


def investigate(payload, profile, tz, verbose):
    """Headless Claude Code investigation. Returns raw answers dict."""
    charter = CHARTER.read_text()
    session_id = str(uuid.uuid4())
    context = ""
    if payload.get("context"):
        context = ("\nContext from the previous round (your earlier sanitized "
                   "answers, provided by the manager):\n"
                   + json.dumps(payload["context"], indent=1, ensure_ascii=False) + "\n")
    day = str(payload.get("date"))  # server-supplied: validated before it becomes a file name
    summary_file = DAYS_DIR / f"{day}.summary.json"
    if DATE_RE.fullmatch(day) and summary_file.exists():
        context += f"""
## The daily summary this Mac already sent for {day} (the Manager AI has read it)
{summary_file.read_text().strip()}

Start from this summary: answer from it wherever it covers a question, and check raw
sources only for what it does not answer. The raw collector output of that day may be on
disk at {DAYS_DIR / (day + ".json")} — read it before re-running a collector. A source
marked "error" above could not be read, so look at it fresh if a question depends on it.
"""
    prompt = f"""{charter}

## Today's assignment
Investigation date: {payload.get("date")}
Person you represent (calibrate all judgments to this role):
{profile}

TIMEZONE RULE (mandatory): the employee's timezone is **{tz}**, and the machine's
timezone is **{machine_timezone()}**. Convert EVERY timestamp from every source —
calendars, Fireflies, Drive, GitHub, cloud APIs often return UTC or another zone —
to {tz} before reasoning about the day, and state all times in your answers in
{tz}. If a meeting was scheduled in another timezone, say so explicitly.

Plugin directory (run collectors from here): {PLUGIN_ROOT}
Exclude this Claude session id from all analysis (it is the HR run itself,
pass it via --exclude-sessions where supported): {session_id}
{context}
## Questions from the Manager AI
{json.dumps(payload.get("questions", []), indent=1, ensure_ascii=False)}

Investigate now, then output ONLY the answers JSON."""

    result_text = run_claude(prompt, session_id, verbose, "Investigation")
    data = extract_json(result_text)
    if data is None:
        raise RuntimeError(f"No JSON in investigation result: {result_text[:400]}")
    return data


def poll_once(cfg, verbose):
    flush_outbox(cfg)      # answers a crashed or offline run saved but never sent
    flush_summaries(cfg)   # same for daily summaries
    sync_briefs(cfg)       # cheap calls first: a 25-min investigation must not delay them
    heartbeat(cfg)
    prune_days(cfg)
    profile = load_profile()
    _, error = summarize_days(cfg, missing_days(cfg, summary_today(cfg)), profile, verbose)
    _, body = api(cfg, "GET", "/v1/questions/pending")
    sets = (body or {}).get("question_sets", [])
    if not sets:
        log("No pending questions.")
    done = 0
    for qs in sets:
        sid = str(qs.get("id"))
        if not SET_ID_RE.fullmatch(sid) or (OUTBOX_DIR / f"{sid}.json").exists() \
                or (AUDIT_DIR / f"{sid}.rejected.json").exists():  # re-investigating would repeat the rejection
            continue
        log(f"Investigating {qs.get('date')} round {qs.get('round', 1)} ({sid}) …")
        try:
            raw = investigate(qs, profile, cfg["timezone"], verbose)
        except Exception as e:  # noqa: BLE001 — one failed set must not block the others
            log(f"Investigation failed for {sid}: {e}")
            # class name only: the message can carry unsanitized investigation output
            error = f"investigation failed ({e.__class__.__name__})"
            continue
        (AUDIT_DIR / f"{sid}.raw.json").write_text(json.dumps(raw, indent=1, ensure_ascii=False))
        clean, violations = sanitize_answers(to_contract(raw))
        envelope = {
            "employee_id": cfg["employee_id"],
            "date": qs.get("date"),
            "round": qs.get("round", 1),
            "timezone": cfg["timezone"],
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "agent_version": VERSION,
            "privacy_redactions": violations,
            "answers": clean,
        }
        write_private(OUTBOX_DIR / f"{sid}.json", json.dumps(envelope, indent=1, ensure_ascii=False))
        log(f"Answered {sid}" + (f" (privacy gate redacted: {violations})" if violations else ""))
        flush_outbox(cfg)
        done += 1
    if sets or error:
        heartbeat(cfg, "error" if error else "ok", error)
    return done


def leave(cfg):
    try:
        api(cfg, "DELETE", "/v1/devices/me")
    except Unlinked:
        pass  # already revoked on the server
    CONFIG_FILE.unlink()
    log(f"This Mac is unlinked from Yolk. Briefs, reports and the audit trail stay in {YOLK_HOME}.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", nargs="?", default="poll",
                    choices=("poll", "claim", "sync", "leave", "summarize"))
    ap.add_argument("code", nargs="?", help="invite code (claim only)")
    ap.add_argument("--date", help="YYYY-MM-DD (summarize only; default: every missing past day)")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()
    if a.date and a.action != "summarize":
        ap.error("--date only applies to summarize")
    ensure_dirs()
    if a.action != "leave":
        heal_schedule()
    try:
        if a.action == "claim":
            if not a.code:
                ap.error("claim needs the invite code")
            claim(a.code)
            return
        cfg = load_config()
        if a.action == "leave":
            leave(cfg)
        elif a.action == "sync":
            sync_briefs(cfg)
            heartbeat(cfg)
        else:
            with open(YOLK_HOME / ".lock", "w") as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    log("Another run is already in progress; it posts its answers and summaries when done.")
                    return
                if a.action == "summarize":
                    summarize_cli(cfg, a.date, a.verbose)
                else:
                    n = poll_once(cfg, a.verbose)
                    log(f"Done — {n} question set(s) answered.")
    except Unlinked:
        CONFIG_FILE.unlink(missing_ok=True)
        raise SystemExit("This Mac is no longer linked to Yolk (the server revoked its token). "
                         "Run /yolk:setup with a new invite code from your manager.")
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        raise SystemExit(f"Cannot reach the Yolk server ({e}). Unsent answers and summaries stay in "
                         f"{OUTBOX_DIR} and go out on the next poll.")


if __name__ == "__main__":
    main()
