"""poll: order of one cycle, status.json phases, briefs, heartbeat."""
import json
import os
import urllib.error
from datetime import date, timedelta

from support import CFG, clean_dirs, fake_collectors
from yolk import activity, api, checkin, claude, config, poll, store


def check_sync_briefs_and_heartbeat_glob():
    clean_dirs()
    real, api.request = api.request, lambda cfg, m, path, body=None, auth=True: (200, {"briefs": [
        {"date": "2026-10-07", "brief": {"v": 1}, "report_markdown": "# old report"}]})
    try:
        poll.sync_briefs(CFG)
    finally:
        api.request = real
    assert json.loads((config.BRIEFS_DIR / "2026-10-07.json").read_text()) == {"v": 1}
    assert not (config.REPORTS_DIR / "2026-10-07.md").exists()          # reports are no longer written
    # 1.4.0 left summary-*.sent.json behind: never "last answered"
    (config.AUDIT_DIR / "qs_1.sent.json").write_text("{}")
    os.utime(config.AUDIT_DIR / "qs_1.sent.json", (1_700_000_000, 1_700_000_000))
    (config.AUDIT_DIR / "summary-2026-10-06.sent.json").write_text("{}")
    sent = []
    api.request = lambda cfg, m, path, body=None, auth=True: (sent.append(body), (204, None))[1]
    try:
        poll.heartbeat(CFG)
    finally:
        api.request = real
    assert sent[0]["last_answered_at"].startswith("2023-11-1"), sent[0]

def check_poll_order(scripts):
    """Answers come first; a question date's summary is built right before its investigation;
    the backfill of the other days comes after the answers; /v1/summaries is never called."""
    clean_dirs()
    today = config.summary_today(CFG)
    day = lambda n: (today - timedelta(days=n)).isoformat()  # noqa: E731
    events, paths = [], []

    def fake_api(cfg, method, path, body=None, auth=True):
        paths.append(path)
        if path.startswith("/v1/questions/pending"):
            return 200, {"question_sets": [{"id": "qs_a", "date": day(3), "round": 1, "questions": []},
                                           {"id": "qs_b", "date": day(400), "round": 1, "questions": []}]}
        if path.startswith("/v1/briefs"):
            return 200, {"briefs": []}
        events.append(("post", path))
        return (201 if "answers" in path else 204), None

    def summary_day(cfg, date, profile, verbose):
        events.append(("summary", date))
        write = config.DAYS_DIR / f"{date}.summary.json"
        write.write_text("{}")

    def investigate(qs, profile, tz, verbose):
        events.append(("investigate", qs["date"]))
        return {"answers": []}

    saved = (api.request, activity.summarize_day, checkin.investigate)
    api.request, activity.summarize_day, checkin.investigate = fake_api, summary_day, investigate
    (config.OUTBOX_DIR / "qs_old.json").write_text("{}")  # an answer saved by an earlier run
    (config.OUTBOX_DIR / "summaries").mkdir()                # 1.4.0 leftover
    (config.OUTBOX_DIR / "summaries" / "2026-10-01.json").write_text("{}")
    try:
        assert poll.poll_once(CFG, False) == 2
    finally:
        api.request, activity.summarize_day, checkin.investigate = saved
    kinds = [e for e in events if e[0] != "post" or "answers" in e[1]]
    assert kinds[0] == ("post", "/v1/questions/qs_old/answers"), kinds                      # old outbox first
    i_sum, i_inv = kinds.index(("summary", day(3))), kinds.index(("investigate", day(3)))
    assert i_sum < i_inv, kinds                                                              # summary before its investigation
    assert ("summary", day(400)) not in kinds and ("investigate", day(400)) in kinds         # outside the window: not built
    last_answer = max(i for i, e in enumerate(kinds) if e[0] == "post")
    backfill = [e for e in kinds if e[0] == "summary" and e[1] != day(3)]
    assert [e[1] for e in backfill] == [day(1), day(2)], backfill                            # at most 2 more, newest first
    assert all(kinds.index(e) > last_answer for e in backfill), kinds                        # only after the answers
    assert not (config.OUTBOX_DIR / "summaries").exists()                                         # leftover removed
    assert not any("/v1/summaries" in p for p in paths + [e[1] for e in events if e[0] == "post"])

def check_status_phases(scripts):
    clean_dirs()
    config.STATUS_FILE.unlink(missing_ok=True)
    today = config.summary_today(CFG)
    qdate = (today - timedelta(days=2)).isoformat()
    marker = "STATUS-LEAK-MARKER"
    seen = []

    def fake_api(cfg, method, path, body=None, auth=True):
        if path.startswith("/v1/questions/pending"):
            return 200, {"question_sets": [{"id": "qs_s", "date": qdate, "round": 1, "questions": []}]}
        return (200, {"briefs": []}) if path.startswith("/v1/briefs") else (201, None)

    def fake_claude(prompt, sid, verbose, what, **kw):
        st = store.read_status()
        seen.append((st["phase"], st["date"]))
        return json.dumps({"overview": marker})

    def investigate(qs, profile, tz, verbose):
        st = store.read_status()
        seen.append((st["phase"], st["date"]))
        assert st["open_check_ins"] == [qdate]
        return {"answers": [{"question_id": "q1", "answer": marker}]}

    saved = (api.request, claude.run_claude, checkin.investigate)
    api.request, claude.run_claude, checkin.investigate = fake_api, fake_claude, investigate
    try:
        with fake_collectors(scripts, claude_sessions="ok.py"):
            poll.poll_once(CFG, False)
    finally:
        api.request, claude.run_claude, checkin.investigate = saved
    assert ("summarizing", qdate) in seen and ("answering", qdate) in seen, seen
    st = store.read_status()
    assert st["phase"] == "idle" and st["date"] is None and st["last_poll_ended"] and st["last_poll_started"]
    assert st["open_check_ins"] == [] and list(st["answered"]) == [qdate]       # answered: no longer open
    assert set(st) <= {"phase", "date", "since", "last_poll_started", "last_poll_ended",
                       "open_check_ins", "last_error", "answered"}, set(st)
    assert marker not in config.STATUS_FILE.read_text()                               # no summary or answer text
    assert config.STATUS_FILE.stat().st_mode & 0o777 == 0o600
    # a failing run records the class name only
    api.request = lambda *a, **k: (_ for _ in ()).throw(urllib.error.URLError("secret-host-detail"))
    try:
        poll.poll_once(CFG, False)
    except urllib.error.URLError:
        pass
    finally:
        api.request = saved[0]
    st = store.read_status()
    assert st["last_error"] == "URLError" and st["phase"] == "idle" and "secret-host" not in config.STATUS_FILE.read_text()
