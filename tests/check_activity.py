"""activity: collect, window, summarize, attempts."""
import json
import os
import tempfile
import time as time_mod
from pathlib import Path

import yolk
from support import CFG, TODAY, REAL_RUN_CLAUDE, clean_dirs, fake_collectors, agent_log
from yolk import activity, checkin, claude, config, store


def check_missing_days():
    clean_dirs()
    last7 = [f"2026-10-0{n}" for n in range(7, 0, -1)]
    assert activity.missing_days(TODAY) == last7                             # nothing local yet: the last 7, newest first
    (config.DAYS_DIR / "2026-10-07.summary.json").write_text("{}")          # a local summary: done
    (config.AUDIT_DIR / "summary-2026-10-06.failed.json").write_text("{}")  # given up: done
    (config.DAYS_DIR / "2026-10-05.json").write_text("{}")                  # raw data alone is not a summary
    (config.AUDIT_DIR / "2026-10-04.sent.json").write_text("{}")            # an ANSWER file is not a summary
    assert activity.missing_days(TODAY) == ["2026-10-05", "2026-10-04", "2026-10-03", "2026-10-02", "2026-10-01"]
    assert activity.in_window("2026-10-01", TODAY) and activity.in_window("2026-10-07", TODAY)
    assert not activity.in_window("2026-09-30", TODAY) and not activity.in_window("2026-10-08", TODAY)  # too old / not finished
    assert not activity.in_window("junk", TODAY) and not activity.in_window("None", TODAY)
    assert config.employee_now({"timezone": "Asia/Jerusalem"}).utcoffset() is not None
    assert config.employee_now({"timezone": "No/Such_Zone"}).utcoffset() is not None  # falls back

def check_collectors():
    scripts = Path(tempfile.mkdtemp(prefix="yolk-coll-"))
    cases = {
        "ok": ("import json;print(json.dumps({'date':'d','active_claude_minutes':5}))", "ok"),
        "empty": ("import json;print(json.dumps({'date':'d','active_blocks':[],'top':{},'n':0,'fmt':'0:00'}))", "empty"),
        "errkey": ("import json;print(json.dumps({'date':'d','error':'no_access'}))", "error"),
        "exit1": ("import sys;sys.exit(1)", "error"),
        "badjson": ("print('not json')", "error"),
        "list": ("print('[1]')", "error"),
        "hang": ("import time;time.sleep(30)", "error"),
    }
    old = config.COLLECTOR_TIMEOUT_S
    config.COLLECTOR_TIMEOUT_S = 2
    try:
        for name, (code, want) in cases.items():
            (scripts / f"{name}.py").write_text(code)
            status, data = activity.run_collector(scripts / f"{name}.py", "2026-10-07")
            assert status == want, (name, status)
            assert want != "error" or "error" in data, (name, data)
    finally:
        config.COLLECTOR_TIMEOUT_S = old
    return scripts

def check_summarize_and_investigate(scripts):
    clean_dirs()
    marker = "ZZ-SUMMARY-MARKER"
    prompts = []
    canned = json.dumps({"overview": f"{marker} day", "active_minutes": 100, "personal_minutes": 10, "debug": 1,
                         "blocks": [{"start": "09:00", "end": "10:00", "kind": "work", "what": marker,
                                     "where": "a", "how": "b", "confidence": "high"}],
                         "work_items": [{"title": marker, "outcome": "o", "evidence": "e"}]})
    claude.run_claude = lambda prompt, sid, verbose, what, **kw: (prompts.append((prompt, kw)), f"Here: {canned}")[1]
    old_dir, old_map = config.COLLECTORS_DIR, dict(activity.COLLECTORS)
    config.COLLECTORS_DIR = scripts
    activity.COLLECTORS.update(claude_sessions="ok.py", screen_time="exit1.py", git="empty.py", browser="errkey.py")
    try:
        activity.summarize_day(CFG, "2026-10-07", "role: tester", False)
    finally:
        config.COLLECTORS_DIR = old_dir
    assert prompts[0][1].get("toolless") is True and "role: tester" in prompts[0][0]
    env = json.loads((config.DAYS_DIR / "2026-10-07.summary.json").read_text())
    assert set(env) == {"timezone", "generated_at", "agent_version", "privacy_redactions", "sources", "summary"}
    assert env["sources"] == {"claude_sessions": "ok", "screen_time": "error", "git": "empty", "browser": "error"}
    assert env["agent_version"] == yolk.VERSION and env["timezone"] == "Asia/Jerusalem"
    assert "debug" not in env["summary"] and env["summary"]["blocks"][0]["what"] == marker
    assert env["generated_at"].endswith(("+03:00", "+02:00"))
    assert not (config.OUTBOX_DIR / "summaries").exists()                                       # nothing queued for sending
    for name in ("2026-10-07.json", "2026-10-07.summary.json"):
        assert (config.DAYS_DIR / name).stat().st_mode & 0o777 == 0o600, name
    raw = json.loads((config.DAYS_DIR / "2026-10-07.json").read_text())
    assert raw["data"]["browser"]["error"] == "no_access" and raw["data"]["screen_time"]["error"] == "exit_1"
    assert marker not in (config.AUDIT_DIR / "agent.log").read_text(), "summary text leaked into agent.log"
    assert CFG["device_token"] not in prompts[0][0]

    # nothing readable: no model run, still a valid summary
    n = len(prompts)
    config.COLLECTORS_DIR = scripts
    activity.COLLECTORS.update(claude_sessions="empty.py", screen_time="empty.py", git="empty.py", browser="empty.py")
    try:
        activity.summarize_day(CFG, "2026-10-06", "role", False)
    finally:
        config.COLLECTORS_DIR = old_dir
        activity.COLLECTORS.update(old_map)
    assert len(prompts) == n
    env = json.loads((config.DAYS_DIR / "2026-10-06.summary.json").read_text())
    assert env["summary"]["blocks"] == [] and set(env["sources"].values()) == {"empty"}

    # investigate(): the day's summary goes into the prompt, once it exists, and only for a real date
    prompts.clear()
    claude.run_claude = lambda prompt, sid, verbose, what, **kw: (prompts.append(prompt), '{"answers": []}')[1]
    checkin.investigate({"date": "2026-10-07", "questions": []}, "role", "Asia/Jerusalem", False)
    assert marker in prompts[0] and "built on this device" in prompts[0]
    checkin.investigate({"date": "2026-10-03", "questions": []}, "role", "Asia/Jerusalem", False)
    assert "built on this device" not in prompts[1]
    checkin.investigate({"date": "../../etc/passwd", "questions": []}, "role", "Asia/Jerusalem", False)
    assert "built on this device" not in prompts[2]

def check_attempt_cap(scripts):
    clean_dirs()
    leak = "SECRET-REPLY-TEXT"
    calls = []

    def fake_claude(prompt, sid, verbose, what, **kw):
        calls.append(1)
        return behaviour()

    days = ["2026-10-07", "2026-10-06"]
    claude.run_claude = fake_claude
    with fake_collectors(scripts, claude_sessions="ok.py"):
        # a bad reply: counted per day, the next day is still tried; the 3rd failure gives the day up
        behaviour = lambda: f"no json here {leak}"  # noqa: E731
        for n in (1, 2, 3):
            built, error = activity.summarize_days(CFG, days, "role", False)
            assert built == 0 and error == "summary failed (BadReply)", (built, error)
            assert len(calls) == 2 * n, calls                       # both days every poll
            assert all((config.DAYS_DIR / f"{d}.attempts").read_text() == str(n) for d in days) or n == 3
        for d in days:
            failed = json.loads((config.AUDIT_DIR / f"summary-{d}.failed.json").read_text())
            assert set(failed) == {"date", "attempts", "last_error", "failed_at"} and failed["attempts"] == 3
            assert failed["last_error"] == "BadReply" and leak not in json.dumps(failed)
        assert leak not in agent_log() and not list(config.DAYS_DIR.glob("*.summary.json"))
        assert activity.missing_days(TODAY) == [f"2026-10-0{n}" for n in (5, 4, 3, 2, 1)]    # 06 and 07 are given up

        # claude exited non-zero (logged out, usage limit, network): NOT counted; the day cools down
        # for 6 h and the OLDER days are still tried (one bad day must not block the week)
        clean_dirs()
        calls.clear()
        bad_day = days[0]

        def behaviour():
            if len(calls) % 2 == 1:   # the 1st call of each poll is the newest day
                raise claude.ClaudeFailed("rc=1")
            return json.dumps({"overview": "fine"})
        built, error = activity.summarize_days(CFG, days, "role", False)
        assert (built, error, len(calls)) == (1, "summary failed (ClaudeFailed)", 2), (built, error, calls)
        assert not list(config.DAYS_DIR.glob("*.attempts")) and (config.DAYS_DIR / f"{bad_day}.cooldown").exists()
        assert (config.DAYS_DIR / f"{days[1]}.summary.json").exists()
        # cooling: not offered again within 6 h, offered again after
        assert bad_day not in activity.missing_days(TODAY) and days[1] not in activity.missing_days(TODAY)
        os.utime(config.DAYS_DIR / f"{bad_day}.cooldown", (time_mod.time() - 7 * 3600,) * 2)
        assert bad_day in activity.missing_days(TODAY)
        os.utime(config.DAYS_DIR / f"{bad_day}.cooldown")
        assert not store.cooling("2026-10-01") and store.cooling(bad_day)

        # a timeout: counted, and the poll stops at once
        clean_dirs()
        calls.clear()

        def behaviour():
            raise claude.ClaudeTimeout("timed out")
        built, error = activity.summarize_days(CFG, days, "role", False)
        assert (built, error, len(calls)) == (0, "summary failed (ClaudeTimeout)", 1), (built, error, calls)
        assert (config.DAYS_DIR / "2026-10-07.attempts").read_text() == "1" and not (config.DAYS_DIR / "2026-10-06.attempts").exists()
        assert "took" in agent_log() and " s." in agent_log()    # run duration is logged (seconds only)

        # claude not installed: nothing counted, the poll stops
        clean_dirs()
        calls.clear()

        def behaviour():
            raise claude.ClaudeUnavailable("no claude")
        built, error = activity.summarize_days(CFG, days, "role", False)
        assert (built, error, len(calls)) == (0, "summary failed (ClaudeUnavailable)", 1)
        assert not list(config.DAYS_DIR.glob("*.attempts")) and not list(config.AUDIT_DIR.glob("*.failed.json"))

        # success clears the count
        (config.DAYS_DIR / "2026-10-07.attempts").write_text("2")
        behaviour = lambda: json.dumps({"overview": "fine"})  # noqa: E731
        built, error = activity.summarize_days(CFG, ["2026-10-07"], "role", False)
        assert (built, error) == (1, None) and not (config.DAYS_DIR / "2026-10-07.attempts").exists()

    # the real helper: a missing claude is ClaudeUnavailable, not a crash
    clean_dirs()
    empty_bin = tempfile.mkdtemp(prefix="yolk-nobin-")
    old_path, os.environ["PATH"] = os.environ["PATH"], empty_bin
    try:
        for kw in ({}, {"toolless": True}):
            try:
                REAL_RUN_CLAUDE("p", "00000000-0000-0000-0000-000000000000", False, "T", **kw)
                raise AssertionError("expected ClaudeUnavailable")
            except claude.ClaudeUnavailable:
                pass
    finally:
        os.environ["PATH"] = old_path
