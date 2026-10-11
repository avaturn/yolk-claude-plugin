"""The daily activity report: collect a day's raw data, have Claude summarize it, store it locally.

  collect     the four collectors run directly (not through the model); raw output stays in
              days/<date>.json (0600). A source is ok, empty (readable, no activity) or error.
  summarize   one tool-less Claude run turns the raw data into the report; with nothing readable
              there is no model run and nothing to hallucinate from.
  window      finished days from today-7 to yesterday, newest first. A day is done once it has a
              report or was given up on; it is left alone while cooling down.

Failure policy, per day (the reason is not the day's fault unless it is a bad reply or a timeout):
  claude missing            stop the run, count nothing
  claude exited non-zero    not counted; the day cools down and the older days are still tried
  timeout                   counted, stop (it would hang again, up to 10 minutes per remaining day)
  bad reply / other         counted, go on to the next day
Three counted failures give the day up (audit/summary-<date>.failed.json).

Nothing here is ever sent to the server.
"""
import json
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta

from . import VERSION, claude, config, privacy, store

# source name in the report -> collector script
COLLECTORS = {"claude_sessions": "claude_sessions.py", "screen_time": "screen_time.py",
              "git": "git_activity.py", "browser": "browser_history.py"}


# ---- collect -----------------------------------------------------------------------------------

def has_activity(v):
    """Any non-zero number, or any element in a list of scalars, anywhere below `date`.
    ponytail: shape-agnostic on purpose (collectors change); strings alone do not count."""
    if isinstance(v, dict):
        return any(has_activity(x) for k, x in v.items() if k != "date")
    if isinstance(v, list):
        return any(has_activity(x) if isinstance(x, (dict, list)) else bool(x) for x in v)
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v != 0


def run_collector(script, date):
    """One collector. Returns (ok|empty|error, data)."""
    try:
        p = subprocess.run([sys.executable, str(script), "--date", date], capture_output=True,
                           text=True, timeout=config.COLLECTOR_TIMEOUT_S, cwd=config.PLUGIN_ROOT)
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
    sources, data = {}, {}
    for name, script in COLLECTORS.items():
        sources[name], data[name] = run_collector(config.COLLECTORS_DIR / script, date)
    raw = {"date": date, "timezone": tz, "sources": sources, "data": data}
    store.write_private(config.DAYS_DIR / f"{date}.json", json.dumps(raw, indent=1, ensure_ascii=False))
    return raw


# ---- the window --------------------------------------------------------------------------------

def in_window(d, today):
    """Finished days from today-7 to yesterday (`d` a date string)."""
    day = config.parse_date(d)
    return bool(day) and today - timedelta(days=config.SUMMARY_BACKFILL_DAYS) <= day < today


def missing_days(today):
    """Days (newest first) still to summarize. `today` is config.summary_today(cfg)."""
    return [d for d in ((today - timedelta(days=i)).isoformat() for i in range(1, config.SUMMARY_BACKFILL_DAYS + 1))
            if not store.day_done(d) and not store.cooling(d)]


# ---- summarize ---------------------------------------------------------------------------------

def summary_prompt(date, profile, tz, raw):
    return f"""{config.SUMMARIZER_CHARTER.read_text()}

## Today's assignment
Day to summarize: {date}
Person you represent (calibrate all judgments to this role):
{profile}

TIMEZONE RULE (mandatory): the employee's timezone is **{tz}**, and the machine's
timezone is **{config.machine_timezone()}**. The collectors cut the day and print times in the
machine's timezone. If the two differ, convert EVERY time to {tz} and state all block
times in {tz}.

Yolk's own headless runs happen in the plugin directory {config.PLUGIN_ROOT}: Claude sessions
whose project path is that directory are the Yolk agent itself, not the person's work.

## Raw collector data for {date} (local only — never repeat it verbatim)
{json.dumps(raw, ensure_ascii=False)}

Summarize now, then output ONLY the summary JSON object."""


def summarize_day(cfg, date, profile, verbose):
    """Collect, summarize, sanitize, save days/<date>.summary.json."""
    tz = cfg["timezone"]
    store.set_phase("collecting", date)
    raw = collect_day(date, tz)
    statuses = raw["sources"]
    if "ok" in statuses.values():
        store.set_phase("summarizing", date)
        started = time.monotonic()
        try:
            result = claude.run_claude(summary_prompt(date, profile, tz, raw), str(uuid.uuid4()), verbose,
                                       "Summarization", toolless=True, max_turns=3,
                                       timeout=config.SUMMARY_TIMEOUT_S)
        finally:  # seconds only: to tune SUMMARY_TIMEOUT_S later
            store.log(f"Summarizer run for {date} took {round(time.monotonic() - started)} s.")
        try:
            parsed = claude.extract_json(result)
        except ValueError:  # json.JSONDecodeError; its message holds positions only
            parsed = None
        if parsed is None:
            raise claude.BadReply("no JSON in the summarization result")
    else:
        parsed = {"overview": "Local activity data was not available for this day."
                  if "error" in statuses.values() else "No activity was recorded on this device."}
    summary, violations = privacy.gate_and_normalize(parsed)
    envelope = {"timezone": tz,
                "generated_at": config.employee_now(cfg).isoformat(timespec="seconds"),
                "agent_version": VERSION, "privacy_redactions": violations,
                "sources": statuses, "summary": summary}
    text = json.dumps(envelope, indent=1, ensure_ascii=False) + "\n"
    store.write_private(config.DAYS_DIR / f"{date}.summary.json", text)
    (config.DAYS_DIR / f"{date}.attempts").unlink(missing_ok=True)
    store.log(f"Summarized {date}: " + " ".join(f"{k}={v}" for k, v in statuses.items())
              + f"; {len(summary['blocks'])} block(s), {len(summary['work_items'])} work item(s)"
              + (f" (privacy gate redacted: {violations})" if violations else ""))


def count_failure(date, e):
    """Count one failed build of `date`; the third gives the day up (labels only, no content)."""
    f = config.DAYS_DIR / f"{date}.attempts"
    try:
        n = int(f.read_text()) + 1
    except (OSError, ValueError):
        n = 1
    store.write_private(f, str(n))
    if n >= config.SUMMARY_MAX_ATTEMPTS:
        store.write_private(config.AUDIT_DIR / f"summary-{date}.failed.json", json.dumps(
            {"date": date, "attempts": n, "last_error": e.__class__.__name__,
             "failed_at": datetime.now().astimezone().isoformat(timespec="seconds")}, indent=1) + "\n")
        store.log(f"Giving up on the summary for {date} after {n} failed attempts "
                  f"(audit/summary-{date}.failed.json); this day goes unsummarized.")
    return n


def summarize_days(cfg, days, profile, verbose):
    """Build one report per day, following the failure policy above. Returns (built, error)."""
    built, error = 0, None
    for d in days:
        try:
            summarize_day(cfg, d, profile, verbose)
        except (claude.ClaudeUnavailable, claude.ClaudeFailed) as e:
            if isinstance(e, claude.ClaudeTimeout):
                n = count_failure(d, e)
                store.log(f"Summary for {d} timed out (attempt {n}/{config.SUMMARY_MAX_ATTEMPTS}).")
                return built, f"summary failed ({e.__class__.__name__})"
            error = f"summary failed ({e.__class__.__name__})"
            if isinstance(e, claude.ClaudeUnavailable):
                store.log(f"Summary for {d} skipped, not counted: {e}")
                return built, error
            store.write_private(config.DAYS_DIR / f"{d}.cooldown", "")
            store.log(f"Summary for {d} skipped for {config.COOLDOWN_S // 3600} h, not counted: {e}")
            continue
        except Exception as e:  # noqa: BLE001 — one bad day must not block the rest
            n = count_failure(d, e)
            store.log(f"Summary for {d} failed (attempt {n}/{config.SUMMARY_MAX_ATTEMPTS}). ({e.__class__.__name__}: {e})")
            error = f"summary failed ({e.__class__.__name__})"
            continue
        built += 1
    return built, error


def summarize_cli(cfg, date, limit, verbose):
    """The `summarize` action: build the reports of D (even if one exists), or the newest `limit`
    missing days."""
    with store.tracked_run():
        today = config.summary_today(cfg)
        if date:
            d = config.parse_date(date)
            if not d or not today - timedelta(days=config.RAW_KEEP_DAYS) <= d < today:
                raise SystemExit(f"--date must be a finished day (before {today}) within the last "
                                 f"{config.RAW_KEEP_DAYS} days.")
            days = [date]
        else:
            days = missing_days(today)[:limit]
        built, error = summarize_days(cfg, days, config.load_profile(), verbose)
        store.log(f"Done — {built} summary(ies) built.")
        store.set_status(last_error=error)
        if error:
            raise SystemExit(error)
