"""One poll cycle, in a fixed order: answers never wait behind a week of backfill.

  1. record whether the job has Full Disk Access (Screen Time)
  2. send answers saved by an earlier run
  3. fetch briefs, heartbeat   (cheap calls first: a 25-minute investigation must not delay them)
  4. prune old raw day files
  5. fetch the pending question sets; answer each (its day's report first)
  6. only now fill in the reports of the other missing days, BACKFILL_PER_POLL at a time
  7. heartbeat again if there was anything to report

What launchd runs every 20 minutes. The caller holds `.lock`.
"""
import json
import shutil
from datetime import datetime

from . import VERSION, activity, api, checkin, config, schedule, store


def sync_briefs(cfg):
    got = 0
    for b in api.briefs(cfg, store.latest_brief()):
        if not config.DATE_RE.fullmatch(str(b.get("date"))):
            continue
        (config.BRIEFS_DIR / f"{b['date']}.json").write_text(
            json.dumps(b.get("brief"), indent=1, ensure_ascii=False) + "\n")
        got += 1
    if got:
        store.log(f"Received {got} brief(s).")


def heartbeat(cfg, status="ok", error=None):
    # 1.4.0 left summary-*.sent.json behind: only answers count as "last answered"
    sent = max((p for p in config.AUDIT_DIR.glob("*.sent.json") if not p.name.startswith("summary-")),
               key=lambda p: p.stat().st_mtime, default=None)
    api.heartbeat(cfg, {
        "agent_version": VERSION, "status": status, "error": error,
        "last_answered_at": datetime.fromtimestamp(sent.stat().st_mtime).astimezone()
                                    .isoformat(timespec="seconds") if sent else None,
        "latest_brief": store.latest_brief()})


def poll_once(cfg, verbose):
    """One cycle; returns how many question sets were answered."""
    with store.tracked_run():
        schedule.record_full_disk_access()
        checkin.flush_outbox(cfg)
        sync_briefs(cfg)
        heartbeat(cfg)
        store.prune_days(cfg)
        shutil.rmtree(config.OUTBOX_DIR / "summaries", ignore_errors=True)  # 1.4.0 queued summaries; never sent now
        profile = config.load_profile()
        today = config.summary_today(cfg)
        error, tried = None, set()
        sets = api.pending(cfg)
        store.set_status(open_check_ins=sorted({str(q.get("date")) for q in sets}))
        if not sets:
            store.log("No pending questions.")
        done = 0
        for question_set in sets:
            answered, summarized, error = checkin.answer(cfg, question_set, profile, today, error, verbose)
            done += answered
            if summarized:
                tried.add(summarized)
        _, err = activity.summarize_days(
            cfg, [d for d in activity.missing_days(today) if d not in tried][:config.BACKFILL_PER_POLL],
            profile, verbose)
        error = error or err
        status = store.read_status()
        store.set_status(last_error=error, open_check_ins=[d for d in status.get("open_check_ins", [])
                                                           if d not in (status.get("answered") or {})])
        if sets or error:
            heartbeat(cfg, "error" if error else "ok", error)
        return done
