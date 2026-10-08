#!/usr/bin/env python3
"""Runnable check of the device runner's daily-summary logic. No pytest, no network, no claude.

    python3 tests/check_runner.py
"""
import contextlib
import io
import json
import os
import plistlib
import shutil
import sys
import tempfile
import urllib.error
import time as time_mod
from datetime import date, datetime, time, timedelta
from pathlib import Path

HOME = Path(tempfile.mkdtemp(prefix="yolk-check-"))
os.environ["YOLK_HOME"] = str(HOME)  # before the import: the runner reads it at import time
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import employee_runner as r  # noqa: E402

r.ensure_dirs()
REAL_RUN_CLAUDE = r.run_claude
CFG = {"server": "http://localhost:1", "device_token": "dvc_x", "employee_id": "e1",
       "timezone": "Asia/Jerusalem"}
TODAY = date(2026, 10, 8)


def clean_dirs():
    for d in (r.OUTBOX_DIR, r.AUDIT_DIR, r.DAYS_DIR, r.BRIEFS_DIR):
        for f in d.glob("*"):
            if f.is_file():
                f.unlink()


def check_contract():
    block = {"start": "09:00", "end": "10:00", "kind": "work", "what": "w", "where": "x", "how": "y",
             "confidence": "high", "secret": "z"}
    out = r.to_summary_contract({"overview": "o", "evil": 1, "active_minutes": 5, "personal_minutes": 1,
                                 "blocks": [block],
                                 "work_items": [{"title": "t", "outcome": "o", "evidence": "e", "extra": 1}]})
    assert set(out) == {"overview", "active_minutes", "personal_minutes", "blocks", "work_items"}, out
    assert set(out["blocks"][0]) == {"start", "end", "kind", "what", "where", "how", "confidence"}
    assert set(out["work_items"][0]) == {"title", "outcome", "evidence"}

    # limits
    big = "a" * 2000
    out = r.to_summary_contract({
        "overview": big,
        "blocks": [{**block, "what": big, "where": big, "how": big, "start": f"{i // 4:02d}:{i % 4 * 15:02d}",
                    "end": f"{i // 4:02d}:{i % 4 * 15 + 14:02d}"} for i in range(60)],
        "work_items": [{"title": big, "outcome": big, "evidence": big}] * 30})
    assert len(out["overview"]) == 1500
    assert len(out["blocks"]) == 48 and len(out["work_items"]) == 20
    assert (len(out["blocks"][0]["what"]), len(out["blocks"][0]["where"]), len(out["blocks"][0]["how"])) == (300, 200, 300)
    assert [len(out["work_items"][0][k]) for k in ("title", "outcome", "evidence")] == [200, 500, 300]

    # enums, times, minutes
    out = r.to_summary_contract({"blocks": [
        {**block, "kind": " Meeting ", "start": "9:40", "confidence": "certain"},
        {**block, "kind": "lunch"},                      # unknown kind: dropped
        {**block, "start": "25:00"},                     # not a time: dropped
        {**block, "start": "10:00", "end": "09:00"},     # ends before it starts: dropped
        {**block, "what": ""},                           # a work block that says nothing: dropped
        "junk", None],
        "active_minutes": "410", "personal_minutes": -5})
    assert [(b["kind"], b["start"], b["confidence"]) for b in out["blocks"]] == [("meeting", "09:40", "low")], out
    assert (out["active_minutes"], out["personal_minutes"]) == (410, 0)
    for v, want in ((12.6, 13), ("abc", 0), (99999, 1440), (float("nan"), 0), (True, 0), (None, 0)):
        assert r.to_summary_contract({"active_minutes": v})["active_minutes"] == want, v

    # personal (and away) detail is stripped
    out = r.to_summary_contract({"blocks": [
        {**block, "kind": "personal", "what": "watching X on YouTube", "where": "youtube.com", "how": "scrolling"},
        {**block, "kind": "away", "start": "11:00", "end": "12:00", "what": "doctor appointment", "where": "clinic"}]})
    for b in out["blocks"]:
        assert (b["what"], b["where"], b["how"]) == (b["kind"], "", ""), b
    assert "YouTube" not in json.dumps(out) and "doctor" not in json.dumps(out)

    # wrapped output and garbage in
    assert r.to_summary_contract({"summary": {"overview": "wrapped"}})["overview"] == "wrapped"
    assert r.to_summary_contract(None) == {"overview": "", "active_minutes": 0, "personal_minutes": 0,
                                           "blocks": [], "work_items": []}

    # the privacy gate runs on the full text, before the limits: a cut secret must not slip past it
    fence = "```\na\nb\nc\nd\n```"
    out, violations = r.build_summary({"overview": "k " + "sk-ant-abcdefghijkl", "blocks": [
        {**block, "what": fence * 20, "where": "w" * 190 + " sk-ant-abcdefghijkl"}]})
    assert "code_block" in violations and "secret_pattern" in violations, violations
    assert len(out["blocks"][0]["what"]) <= 300 and len(out["blocks"][0]["where"]) <= 200
    assert "sk-ant" not in json.dumps(out)


def check_missing_days():
    clean_dirs()
    last7 = [f"2026-10-0{n}" for n in range(7, 0, -1)]
    assert r.missing_days(TODAY) == last7                             # nothing local yet: the last 7, newest first
    (r.DAYS_DIR / "2026-10-07.summary.json").write_text("{}")          # a local summary: done
    (r.AUDIT_DIR / "summary-2026-10-06.failed.json").write_text("{}")  # given up: done
    (r.DAYS_DIR / "2026-10-05.json").write_text("{}")                  # raw data alone is not a summary
    (r.AUDIT_DIR / "2026-10-04.sent.json").write_text("{}")            # an ANSWER file is not a summary
    assert r.missing_days(TODAY) == ["2026-10-05", "2026-10-04", "2026-10-03", "2026-10-02", "2026-10-01"]
    assert r.in_window("2026-10-01", TODAY) and r.in_window("2026-10-07", TODAY)
    assert not r.in_window("2026-09-30", TODAY) and not r.in_window("2026-10-08", TODAY)  # too old / not finished
    assert not r.in_window("junk", TODAY) and not r.in_window("None", TODAY)
    assert r.employee_now({"timezone": "Asia/Jerusalem"}).utcoffset() is not None
    assert r.employee_now({"timezone": "No/Such_Zone"}).utcoffset() is not None  # falls back


def check_claim():
    clean_dirs()
    body = {"device_token": "dvc_new", "employee": {"id": "e1", "name": "T", "timezone": "Asia/Jerusalem",
                                                     "profile_md": "p"}, "poll_interval_s": 1200}
    real, r.api = r.api, lambda cfg, method, path, body_=None, auth=True: (200, body if "claim" in path else {})
    try:
        r.claim("K7QP-3FXA")
    finally:
        r.api = real
    cfg = json.loads(r.CONFIG_FILE.read_text())
    assert "linked_on" not in cfg and r.CONFIG_FILE.stat().st_mode & 0o777 == 0o600
    r.CONFIG_FILE.unlink()


def check_sync_briefs_and_heartbeat_glob():
    clean_dirs()
    real, r.api = r.api, lambda cfg, m, path, body=None, auth=True: (200, {"briefs": [
        {"date": "2026-10-07", "brief": {"v": 1}, "report_markdown": "# old report"}]})
    try:
        r.sync_briefs(CFG)
    finally:
        r.api = real
    assert json.loads((r.BRIEFS_DIR / "2026-10-07.json").read_text()) == {"v": 1}
    assert not (r.REPORTS_DIR / "2026-10-07.md").exists()          # reports are no longer written
    # 1.4.0 left summary-*.sent.json behind: never "last answered"
    (r.AUDIT_DIR / "qs_1.sent.json").write_text("{}")
    os.utime(r.AUDIT_DIR / "qs_1.sent.json", (1_700_000_000, 1_700_000_000))
    (r.AUDIT_DIR / "summary-2026-10-06.sent.json").write_text("{}")
    sent = []
    r.api = lambda cfg, m, path, body=None, auth=True: (sent.append(body), (204, None))[1]
    try:
        r.heartbeat(CFG)
    finally:
        r.api = real
    assert sent[0]["last_answered_at"].startswith("2023-11-1"), sent[0]


def check_latest_brief():
    clean_dirs()
    assert r.latest_brief() is None
    for name in ("2026-10-05.json", "2026-10-07.json", "notes.json", "latest.json", "2026-10-09.summary.json"):
        (r.BRIEFS_DIR / name).write_text("{}")
    assert r.latest_brief() == "2026-10-07"


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
    old = r.COLLECTOR_TIMEOUT_S
    r.COLLECTOR_TIMEOUT_S = 2
    try:
        for name, (code, want) in cases.items():
            (scripts / f"{name}.py").write_text(code)
            status, data = r.run_collector(scripts / f"{name}.py", "2026-10-07")
            assert status == want, (name, status)
            assert want != "error" or "error" in data, (name, data)
    finally:
        r.COLLECTOR_TIMEOUT_S = old
    return scripts


def check_heartbeat_and_answers_flush():
    clean_dirs()
    posts = []
    real, r.api = r.api, lambda cfg, m, path, body=None, auth=True: (posts.append(path), (201, None))[1]
    try:
        (r.OUTBOX_DIR / "qs_1.json").write_text("{}")
        r.flush_outbox(CFG)
    finally:
        r.api = real
    assert posts == ["/v1/questions/qs_1/answers"] and (r.AUDIT_DIR / "qs_1.sent.json").exists()
    os.utime(r.AUDIT_DIR / "qs_1.sent.json", (1_700_000_000, 1_700_000_000))
    sent = []
    r.api = lambda cfg, m, path, body=None, auth=True: (sent.append(body), (204, None))[1]
    try:
        r.heartbeat(CFG)
    finally:
        r.api = real
    assert sent[0]["last_answered_at"].startswith("2023-11-1"), sent[0]
    assert not hasattr(r, "flush_summaries") and not hasattr(r, "SUMMARY_OUTBOX_DIR")


def check_summarize_and_investigate(scripts):
    clean_dirs()
    marker = "ZZ-SUMMARY-MARKER"
    prompts = []
    canned = json.dumps({"overview": f"{marker} day", "active_minutes": 100, "personal_minutes": 10, "debug": 1,
                         "blocks": [{"start": "09:00", "end": "10:00", "kind": "work", "what": marker,
                                     "where": "a", "how": "b", "confidence": "high"}],
                         "work_items": [{"title": marker, "outcome": "o", "evidence": "e"}]})
    r.run_claude = lambda prompt, sid, verbose, what, **kw: (prompts.append((prompt, kw)), f"Here: {canned}")[1]
    old_dir, old_map = r.COLLECTORS_DIR, dict(r.COLLECTORS)
    r.COLLECTORS_DIR = scripts
    r.COLLECTORS.update(claude_sessions="ok.py", screen_time="exit1.py", git="empty.py", browser="errkey.py")
    try:
        r.summarize_day(CFG, "2026-10-07", "role: tester", False)
    finally:
        r.COLLECTORS_DIR = old_dir
    assert prompts[0][1].get("toolless") is True and "role: tester" in prompts[0][0]
    env = json.loads((r.DAYS_DIR / "2026-10-07.summary.json").read_text())
    assert set(env) == {"timezone", "generated_at", "agent_version", "privacy_redactions", "sources", "summary"}
    assert env["sources"] == {"claude_sessions": "ok", "screen_time": "error", "git": "empty", "browser": "error"}
    assert env["agent_version"] == r.VERSION and env["timezone"] == "Asia/Jerusalem"
    assert "debug" not in env["summary"] and env["summary"]["blocks"][0]["what"] == marker
    assert env["generated_at"].endswith(("+03:00", "+02:00"))
    assert not (r.OUTBOX_DIR / "summaries").exists()                                       # nothing queued for sending
    for name in ("2026-10-07.json", "2026-10-07.summary.json"):
        assert (r.DAYS_DIR / name).stat().st_mode & 0o777 == 0o600, name
    raw = json.loads((r.DAYS_DIR / "2026-10-07.json").read_text())
    assert raw["data"]["browser"]["error"] == "no_access" and raw["data"]["screen_time"]["error"] == "exit_1"
    assert marker not in (r.AUDIT_DIR / "agent.log").read_text(), "summary text leaked into agent.log"
    assert CFG["device_token"] not in prompts[0][0]

    # nothing readable: no model run, still a valid summary
    n = len(prompts)
    r.COLLECTORS_DIR = scripts
    r.COLLECTORS.update(claude_sessions="empty.py", screen_time="empty.py", git="empty.py", browser="empty.py")
    try:
        r.summarize_day(CFG, "2026-10-06", "role", False)
    finally:
        r.COLLECTORS_DIR = old_dir
        r.COLLECTORS.update(old_map)
    assert len(prompts) == n
    env = json.loads((r.DAYS_DIR / "2026-10-06.summary.json").read_text())
    assert env["summary"]["blocks"] == [] and set(env["sources"].values()) == {"empty"}

    # investigate(): the day's summary goes into the prompt, once it exists, and only for a real date
    prompts.clear()
    r.run_claude = lambda prompt, sid, verbose, what, **kw: (prompts.append(prompt), '{"answers": []}')[1]
    r.investigate({"date": "2026-10-07", "questions": []}, "role", "Asia/Jerusalem", False)
    assert marker in prompts[0] and "built on this device" in prompts[0]
    r.investigate({"date": "2026-10-03", "questions": []}, "role", "Asia/Jerusalem", False)
    assert "built on this device" not in prompts[1]
    r.investigate({"date": "../../etc/passwd", "questions": []}, "role", "Asia/Jerusalem", False)
    assert "built on this device" not in prompts[2]


@contextlib.contextmanager
def fake_collectors(scripts, **mapping):
    old_dir, old_map = r.COLLECTORS_DIR, dict(r.COLLECTORS)
    r.COLLECTORS_DIR = scripts
    r.COLLECTORS.update(claude_sessions="empty.py", screen_time="empty.py", git="empty.py", browser="empty.py")
    r.COLLECTORS.update(mapping)
    try:
        yield
    finally:
        r.COLLECTORS_DIR = old_dir
        r.COLLECTORS.clear()
        r.COLLECTORS.update(old_map)


def agent_log():
    return (r.AUDIT_DIR / "agent.log").read_text() if (r.AUDIT_DIR / "agent.log").exists() else ""


def check_atomic_and_corrupt_outbox():
    clean_dirs()
    f = r.AUDIT_DIR / "x.json"
    r.write_private(f, "old")
    r.write_private(f, "new")
    assert f.read_text() == "new" and f.stat().st_mode & 0o777 == 0o600
    try:
        r.write_private(f, 12345)  # fails halfway: the old content and no temp file must survive
    except TypeError:
        pass
    assert f.read_text() == "new"
    assert [p.name for p in r.AUDIT_DIR.iterdir() if p.name != "agent.log"] == ["x.json"], list(r.AUDIT_DIR.iterdir())
    f.unlink()

    # unreadable outbox files are set aside; the poll goes on and the good ones are sent
    posts = []
    real, r.api = r.api, lambda cfg, m, path, body=None, auth=True: (posts.append(path), (201, None))[1]
    try:
        (r.OUTBOX_DIR / "qs_bad.json").write_text("{not json")
        (r.OUTBOX_DIR / "qs_ok.json").write_text("{}")
        (r.OUTBOX_DIR / "qs_utf8.json").write_bytes(b"\xff\xfe\x00")  # not UTF-8
        (r.OUTBOX_DIR / "qs_list.json").write_text("[1]")               # not an object
        r.flush_outbox(CFG)
    finally:
        r.api = real
    assert posts == ["/v1/questions/qs_ok/answers"], posts
    for name in ("qs_bad", "qs_utf8", "qs_list"):
        assert (r.AUDIT_DIR / f"{name}.rejected.json").exists(), name
    assert (r.AUDIT_DIR / "qs_ok.sent.json").exists() and not list(r.OUTBOX_DIR.glob("*.json"))
    assert "unreadable" in agent_log()

    # an outbox body written by the poller is atomic and private too
    r.write_private(r.OUTBOX_DIR / "qs_1.json", "{}")
    assert (r.OUTBOX_DIR / "qs_1.json").stat().st_mode & 0o777 == 0o600


def check_attempt_cap(scripts):
    clean_dirs()
    leak = "SECRET-REPLY-TEXT"
    calls = []

    def claude(prompt, sid, verbose, what, **kw):
        calls.append(1)
        return behaviour()

    days = ["2026-10-07", "2026-10-06"]
    r.run_claude = claude
    with fake_collectors(scripts, claude_sessions="ok.py"):
        # a bad reply: counted per day, the next day is still tried; the 3rd failure gives the day up
        behaviour = lambda: f"no json here {leak}"  # noqa: E731
        for n in (1, 2, 3):
            built, error = r.summarize_days(CFG, days, "role", False)
            assert built == 0 and error == "summary failed (BadReply)", (built, error)
            assert len(calls) == 2 * n, calls                       # both days every poll
            assert all((r.DAYS_DIR / f"{d}.attempts").read_text() == str(n) for d in days) or n == 3
        for d in days:
            failed = json.loads((r.AUDIT_DIR / f"summary-{d}.failed.json").read_text())
            assert set(failed) == {"date", "attempts", "last_error", "failed_at"} and failed["attempts"] == 3
            assert failed["last_error"] == "BadReply" and leak not in json.dumps(failed)
        assert leak not in agent_log() and not list(r.DAYS_DIR.glob("*.summary.json"))
        assert r.missing_days(TODAY) == [f"2026-10-0{n}" for n in (5, 4, 3, 2, 1)]    # 06 and 07 are given up

        # claude exited non-zero (logged out, usage limit, network): NOT counted; the day cools down
        # for 6 h and the OLDER days are still tried (one bad day must not block the week)
        clean_dirs()
        calls.clear()
        bad_day = days[0]

        def behaviour():
            if len(calls) % 2 == 1:   # the 1st call of each poll is the newest day
                raise r.ClaudeFailed("rc=1")
            return json.dumps({"overview": "fine"})
        built, error = r.summarize_days(CFG, days, "role", False)
        assert (built, error, len(calls)) == (1, "summary failed (ClaudeFailed)", 2), (built, error, calls)
        assert not list(r.DAYS_DIR.glob("*.attempts")) and (r.DAYS_DIR / f"{bad_day}.cooldown").exists()
        assert (r.DAYS_DIR / f"{days[1]}.summary.json").exists()
        # cooling: not offered again within 6 h, offered again after
        assert bad_day not in r.missing_days(TODAY) and days[1] not in r.missing_days(TODAY)
        os.utime(r.DAYS_DIR / f"{bad_day}.cooldown", (time_mod.time() - 7 * 3600,) * 2)
        assert bad_day in r.missing_days(TODAY)
        os.utime(r.DAYS_DIR / f"{bad_day}.cooldown")
        assert not r.cooling("2026-10-01") and r.cooling(bad_day)

        # a timeout: counted, and the poll stops at once
        clean_dirs()
        calls.clear()

        def behaviour():
            raise r.ClaudeTimeout("timed out")
        built, error = r.summarize_days(CFG, days, "role", False)
        assert (built, error, len(calls)) == (0, "summary failed (ClaudeTimeout)", 1), (built, error, calls)
        assert (r.DAYS_DIR / "2026-10-07.attempts").read_text() == "1" and not (r.DAYS_DIR / "2026-10-06.attempts").exists()
        assert "took" in agent_log() and " s." in agent_log()    # run duration is logged (seconds only)

        # claude not installed: nothing counted, the poll stops
        clean_dirs()
        calls.clear()

        def behaviour():
            raise r.ClaudeUnavailable("no claude")
        built, error = r.summarize_days(CFG, days, "role", False)
        assert (built, error, len(calls)) == (0, "summary failed (ClaudeUnavailable)", 1)
        assert not list(r.DAYS_DIR.glob("*.attempts")) and not list(r.AUDIT_DIR.glob("*.failed.json"))

        # success clears the count
        (r.DAYS_DIR / "2026-10-07.attempts").write_text("2")
        behaviour = lambda: json.dumps({"overview": "fine"})  # noqa: E731
        built, error = r.summarize_days(CFG, ["2026-10-07"], "role", False)
        assert (built, error) == (1, None) and not (r.DAYS_DIR / "2026-10-07.attempts").exists()

    # the real helper: a missing claude is ClaudeUnavailable, not a crash
    clean_dirs()
    empty_bin = tempfile.mkdtemp(prefix="yolk-nobin-")
    old_path, os.environ["PATH"] = os.environ["PATH"], empty_bin
    try:
        for kw in ({}, {"toolless": True}):
            try:
                REAL_RUN_CLAUDE("p", "00000000-0000-0000-0000-000000000000", False, "T", **kw)
                raise AssertionError("expected ClaudeUnavailable")
            except r.ClaudeUnavailable:
                pass
    finally:
        os.environ["PATH"] = old_path


def check_claude_flags():
    bindir = Path(tempfile.mkdtemp(prefix="yolk-bin-"))
    out = bindir / "argv.json"
    fake = bindir / "claude"
    fake.write_text("#!/usr/bin/env python3\nimport json,sys\nopen(%r,'w').write(json.dumps(sys.argv[1:]))\n"
                    "print(json.dumps({'type':'result','result':'{}'}))\n" % str(out))
    fake.chmod(0o755)
    old_path, os.environ["PATH"] = os.environ["PATH"], f"{bindir}:{os.environ['PATH']}"
    try:
        REAL_RUN_CLAUDE("PROMPT", "11111111-1111-1111-1111-111111111111", False, "Investigation")
        full = json.loads(out.read_text())
        REAL_RUN_CLAUDE("PROMPT", "11111111-1111-1111-1111-111111111111", False, "Summarization", toolless=True)
        bare = json.loads(out.read_text())
    finally:
        os.environ["PATH"] = old_path
    assert "--no-session-persistence" in full and "--no-session-persistence" in bare
    assert "--dangerously-skip-permissions" in full and "PROMPT" in full and "--tools" not in full
    assert "--dangerously-skip-permissions" not in bare and "PROMPT" not in bare            # stdin, not argv
    assert bare[bare.index("--tools") + 1] == "" and "--strict-mcp-config" in bare
    for argv in (full, bare):  # both runs: Opus 5.5, high effort
        assert argv[argv.index("--model") + 1] == "claude-opus-5-5" and argv[argv.index("--effort") + 1] == "high"


def check_day_boundary():
    clean_dirs()
    machine_today = datetime.now().date()
    real = r.employee_now
    try:
        r.employee_now = lambda cfg: datetime.combine(machine_today + timedelta(days=1), time(1, 0))
        assert r.summary_today(CFG) == machine_today                      # employee's day is ahead
        r.employee_now = lambda cfg: datetime.combine(machine_today - timedelta(days=1), time(23, 0))
        assert r.summary_today(CFG) == machine_today - timedelta(days=1)  # machine's day is ahead
        r.employee_now = lambda cfg: datetime.combine(machine_today + timedelta(days=1), time(1, 0))
        try:  # "today" on the machine has not ended: refused, as the server would 422 it
            r.summarize_cli(CFG, machine_today.isoformat(), 7, False)
            raise AssertionError("expected SystemExit")
        except SystemExit as e:
            assert "--date" in str(e)
    finally:
        r.employee_now = real


def check_prune():
    clean_dirs()
    today = r.summary_today(CFG)
    old, edge, new = ((today - timedelta(days=n)).isoformat() for n in (15, 14, 1))
    names = [f"{old}.json", f"{old}.summary.json", f"{old}.attempts", f"{old}.cooldown", f"{edge}.json", f"{new}.json",
             "notes.txt", "short"]
    for n in names:
        (r.DAYS_DIR / n).write_text("x")
    r.prune_days(CFG)
    assert sorted(p.name for p in r.DAYS_DIR.iterdir()) == sorted([f"{edge}.json", f"{new}.json", "notes.txt", "short"])


def check_heal_schedule():
    clean_dirs()
    tmp = Path(tempfile.mkdtemp(prefix="yolk-plist-"))
    real_plist, real_install = r.LAUNCH_PLIST, r.install_schedule
    r.LAUNCH_PLIST = tmp / "com.yolk.agent.plist"  # never the real ~/Library/LaunchAgents
    calls = []
    r.install_schedule = lambda: (calls.append(1), result[0])[1]
    result = [True]

    real_root, real_plugins = r.PLUGIN_ROOT, r.INSTALLED_PLUGINS_DIR
    r.INSTALLED_PLUGINS_DIR = tmp / "plugins"           # pretend this copy is the installed plugin
    r.PLUGIN_ROOT = r.INSTALLED_PLUGINS_DIR / "cache" / "yolk"

    def plist(*args, home=None):
        r.LAUNCH_PLIST.write_bytes(plistlib.dumps({
            "Label": "com.yolk.agent", "ProgramArguments": list(args),
            "EnvironmentVariables": {"YOLK_HOME": home or str(r.YOLK_HOME)}}))

    xpc = os.environ.pop("XPC_SERVICE_NAME", None)
    try:
        r.heal_schedule()                                                # no plist, not linked: nothing is installed
        assert not calls and not r.LAUNCH_PLIST.exists()
        launcher = r.YOLK_HOME / "bin" / "launcher.py"
        launcher.parent.mkdir(exist_ok=True)
        launcher.write_bytes((r.HERE / "launcher.py").read_bytes())      # the installed copy is current
        plist("/usr/bin/python3", str(launcher))                          # current: untouched
        r.heal_schedule()
        assert not calls
        link = tmp / "linked-bin"
        link.symlink_to(launcher.parent)
        plist("/usr/bin/python3", str(link / "launcher.py"))              # same file through a symlink: current
        r.heal_schedule()
        assert not calls
        # a launcher copy that differs from this version's is re-copied (install re-runs)
        plist("/usr/bin/python3", str(launcher))
        launcher.write_text("# an older launcher\n")
        r.heal_schedule()
        assert len(calls) == 1 and "outdated launcher" in agent_log()
        calls.clear()
        launcher.write_bytes((r.HERE / "launcher.py").read_bytes())
        before = agent_log()
        # a version-pinned plist (1.3 - 1.6.0) migrates to the launcher
        plist("/usr/bin/python3", "/old/cache/yolk/yolk/1.3.1/scripts/employee_runner.py")  # stale
        os.environ["XPC_SERVICE_NAME"] = "com.yolk.agent"                 # started by launchd itself: never reinstall
        r.heal_schedule()
        assert not calls
        del os.environ["XPC_SERVICE_NAME"]
        real_ppid, os.getppid = os.getppid, lambda: 1                    # parent is launchd (pid 1): same
        try:
            r.heal_schedule()
        finally:
            os.getppid = real_ppid
        assert not calls
        r.heal_schedule()
        assert len(calls) == 1
        added = agent_log()[len(before):].strip().splitlines()
        assert len(added) == 1 and "did not use the launcher" in added[0], added          # exactly one log line
        # a dev/test run must never re-point the real poller (install_schedule.sh would bake in ITS env)
        plist("/old/x/employee_runner.py", home="/some/other/yolk-home")   # the plist belongs to another YOLK_HOME
        r.heal_schedule()
        assert len(calls) == 1
        plist("/old/x/employee_runner.py")
        r.PLUGIN_ROOT = real_root                                          # this copy is not under ~/.claude/plugins
        r.heal_schedule()
        assert len(calls) == 1
        r.PLUGIN_ROOT = r.INSTALLED_PLUGINS_DIR / "cache" / "yolk"
        r.LAUNCH_PLIST.write_text("not a plist")                           # unreadable: cannot prove it is ours
        r.heal_schedule()
        assert len(calls) == 1 and "plist unreadable: run install_schedule.sh" in agent_log()
        plist("/old/x/employee_runner.py")
        r.heal_schedule()
        assert len(calls) == 2
        result[0] = False                                                  # a failing install is logged, not raised
        r.heal_schedule()
        assert len(calls) == 3 and "could not update it" in agent_log()
        # wired into main() for every action but leave
        seen = []
        real_heal, r.heal_schedule = r.heal_schedule, lambda: seen.append(1)
        real_cfg, r.load_config = r.load_config, lambda: CFG
        real_sync, real_hb, real_leave = r.sync_briefs, r.heartbeat, r.leave
        r.sync_briefs = r.heartbeat = r.leave = lambda *a, **k: None
        try:
            for action in ("sync", "leave"):
                sys.argv = ["employee_runner.py", action]
                r.main()
            assert seen == [1], seen
        finally:
            r.heal_schedule, r.load_config = real_heal, real_cfg
            r.sync_briefs, r.heartbeat, r.leave = real_sync, real_hb, real_leave
    finally:
        r.LAUNCH_PLIST, r.install_schedule = real_plist, real_install
        r.PLUGIN_ROOT, r.INSTALLED_PLUGINS_DIR = real_root, real_plugins
        os.environ.pop("XPC_SERVICE_NAME", None)
        if xpc is not None:
            os.environ["XPC_SERVICE_NAME"] = xpc


def check_poll_order(scripts):
    """Answers come first; a question date's summary is built right before its investigation;
    the backfill of the other days comes after the answers; /v1/summaries is never called."""
    clean_dirs()
    today = r.summary_today(CFG)
    day = lambda n: (today - timedelta(days=n)).isoformat()  # noqa: E731
    events, paths = [], []

    def api(cfg, method, path, body=None, auth=True):
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
        write = r.DAYS_DIR / f"{date}.summary.json"
        write.write_text("{}")

    def investigate(qs, profile, tz, verbose):
        events.append(("investigate", qs["date"]))
        return {"answers": []}

    saved = (r.api, r.summarize_day, r.investigate)
    r.api, r.summarize_day, r.investigate = api, summary_day, investigate
    (r.OUTBOX_DIR / "qs_old.json").write_text("{}")  # an answer saved by an earlier run
    (r.OUTBOX_DIR / "summaries").mkdir()                # 1.4.0 leftover
    (r.OUTBOX_DIR / "summaries" / "2026-10-01.json").write_text("{}")
    try:
        assert r.poll_once(CFG, False) == 2
    finally:
        r.api, r.summarize_day, r.investigate = saved
    kinds = [e for e in events if e[0] != "post" or "answers" in e[1]]
    assert kinds[0] == ("post", "/v1/questions/qs_old/answers"), kinds                      # old outbox first
    i_sum, i_inv = kinds.index(("summary", day(3))), kinds.index(("investigate", day(3)))
    assert i_sum < i_inv, kinds                                                              # summary before its investigation
    assert ("summary", day(400)) not in kinds and ("investigate", day(400)) in kinds         # outside the window: not built
    last_answer = max(i for i, e in enumerate(kinds) if e[0] == "post")
    backfill = [e for e in kinds if e[0] == "summary" and e[1] != day(3)]
    assert [e[1] for e in backfill] == [day(1), day(2)], backfill                            # at most 2 more, newest first
    assert all(kinds.index(e) > last_answer for e in backfill), kinds                        # only after the answers
    assert not (r.OUTBOX_DIR / "summaries").exists()                                         # leftover removed
    assert not any("/v1/summaries" in p for p in paths + [e[1] for e in events if e[0] == "post"])


def check_heal_installs_missing_plist():
    clean_dirs()
    tmp = Path(tempfile.mkdtemp(prefix="yolk-plist-"))
    saved = (r.LAUNCH_PLIST, r.install_schedule, r.PLUGIN_ROOT, r.INSTALLED_PLUGINS_DIR, r.DEFAULT_YOLK_HOME)
    env_home = os.environ.get("YOLK_HOME")
    r.LAUNCH_PLIST = tmp / "com.yolk.agent.plist"  # never the real ~/Library/LaunchAgents
    r.INSTALLED_PLUGINS_DIR = tmp / "plugins"
    good_root = r.INSTALLED_PLUGINS_DIR / "cache" / "yolk"
    r.PLUGIN_ROOT, r.DEFAULT_YOLK_HOME = good_root, r.YOLK_HOME  # the temp home plays the default home
    calls = []
    r.install_schedule = lambda: (calls.append(1), True)[1]
    xpc = os.environ.pop("XPC_SERVICE_NAME", None)

    def attempt(expect, why):
        before = len(calls)
        r.heal_schedule()
        assert len(calls) - before == expect, why

    try:
        r.CONFIG_FILE.unlink(missing_ok=True)
        attempt(0, "not linked (before claim wrote the config, or after leave)")
        r.CONFIG_FILE.write_text("{}")
        attempt(1, "linked + installed copy + default home + not launchd: install the missing plist")
        os.environ["YOLK_HOME"] = str(tmp / "elsewhere")
        attempt(0, "YOLK_HOME env overridden")
        os.environ["YOLK_HOME"] = env_home
        r.DEFAULT_YOLK_HOME = tmp / "not-the-home"
        attempt(0, "YOLK_HOME is not the default home")
        r.DEFAULT_YOLK_HOME = r.YOLK_HOME
        r.PLUGIN_ROOT = saved[2]
        attempt(0, "running from a non-installed copy (dev checkout)")
        r.PLUGIN_ROOT = good_root
        os.environ["XPC_SERVICE_NAME"] = "com.yolk.agent"
        attempt(0, "launched by launchd")
        del os.environ["XPC_SERVICE_NAME"]
        # after leave: agent.json is gone, so nothing brings the plist back
        r.CONFIG_FILE.unlink()
        attempt(0, "after leave")
        # leave itself never heals
        r.CONFIG_FILE.write_text("{}")
        seen = []
        real_heal, r.heal_schedule = r.heal_schedule, lambda: seen.append(1)
        real_cfg, r.load_config, real_leave, r.leave = r.load_config, lambda: CFG, r.leave, lambda cfg: r.CONFIG_FILE.unlink()
        try:
            sys.argv = ["employee_runner.py", "leave"]
            r.main()
        finally:
            r.heal_schedule, r.load_config, r.leave = real_heal, real_cfg, real_leave
        assert not seen and not r.CONFIG_FILE.exists()
        attempt(0, "after leave, a later run")
    finally:
        r.LAUNCH_PLIST, r.install_schedule, r.PLUGIN_ROOT, r.INSTALLED_PLUGINS_DIR, r.DEFAULT_YOLK_HOME = saved
        r.CONFIG_FILE.unlink(missing_ok=True)
        if env_home is not None:
            os.environ["YOLK_HOME"] = env_home
        if xpc is not None:
            os.environ["XPC_SERVICE_NAME"] = xpc


def check_launcher():
    import importlib.util
    spec = importlib.util.spec_from_file_location("yolk_launcher", r.HERE / "launcher.py")
    L = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(L)
    plugins = Path(tempfile.mkdtemp(prefix="yolk-cache-"))
    assert L.find_runner(plugins) is None                                       # no cache at all
    cache = plugins / "cache" / "yolk" / "yolk"
    cache.mkdir(parents=True)
    assert L.find_runner(plugins) is None                                       # empty cache

    def make(version, orphaned=False, runner=True):
        d = cache / version
        (d / "scripts").mkdir(parents=True)
        if runner:
            (d / "scripts" / "employee_runner.py").write_text("")
        if orphaned:
            (d / ".orphaned_at").write_text("1")
        return d
    make("1.5.0"); make("1.6.0", orphaned=True); make("1.6.1"); make("1.10.0"); make("9.9.9", runner=False)
    assert L.find_runner(plugins).parent.parent.name == "1.10.0"                # numeric, not string, order
    (cache / "1.10.0" / ".orphaned_at").write_text("1")
    assert L.find_runner(plugins).parent.parent.name == "1.6.1"                 # orphaned and runner-less skipped
    # installed_plugins.json: the recorded active install wins when it is usable
    (plugins / "installed_plugins.json").write_text(json.dumps({"version": 2, "plugins": {"yolk@yolk": [
        {"scope": "user", "installPath": str(cache / "1.5.0"), "version": "1.5.0"}], "other@x": [{"installPath": "/zzz"}]}}))
    assert L.find_runner(plugins).parent.parent.name == "1.5.0"
    (plugins / "installed_plugins.json").write_text(json.dumps({"plugins": {"yolk@yolk": [
        {"installPath": str(cache / "1.10.0")}]}}))                             # recorded but orphaned: fall back
    assert L.find_runner(plugins).parent.parent.name == "1.6.1"
    (plugins / "installed_plugins.json").write_text("{broken")                  # unreadable: fall back
    assert L.find_runner(plugins).parent.parent.name == "1.6.1"
    # nothing found: one log line in agent.log, exit 0 (no exec)
    home = Path(tempfile.mkdtemp(prefix="yolk-lh-"))
    old = (L.find_runner, os.environ.get("YOLK_HOME"))
    L.find_runner = lambda: None
    os.environ["YOLK_HOME"] = str(home)
    try:
        L.main()
    finally:
        L.find_runner = old[0]
        os.environ["YOLK_HOME"] = old[1]
    lines = (home / "audit" / "agent.log").read_text().strip().splitlines()
    assert len(lines) == 1 and "no installed Yolk plugin" in lines[0]
    # the exec hands the arguments over to the found runner
    out = plugins / "out.txt"
    (cache / "1.6.1" / "scripts" / "employee_runner.py").write_text(
        f"import sys; open({str(out)!r}, 'w').write(' '.join(sys.argv[1:]))")
    import subprocess
    env = dict(os.environ, HOME=str(plugins / "fakehome"))
    (plugins / "fakehome" / ".claude").mkdir(parents=True)
    (plugins / "fakehome" / ".claude" / "plugins").symlink_to(plugins)
    subprocess.run([sys.executable, str(r.HERE / "launcher.py"), "summarize", "--limit", "1"], env=env, timeout=30)
    assert out.read_text() == "summarize --limit 1"


def check_install_script_plist():
    """Run the real install_schedule.sh under a temp HOME with a stub launchctl: never the real launchd."""
    import subprocess
    home = Path(tempfile.mkdtemp(prefix="yolk-home-"))
    stub = home / "bin"
    stub.mkdir()
    (stub / "launchctl").write_text("#!/bin/sh\necho \"$@\" >> \"$HOME/launchctl.log\"\n")
    (stub / "launchctl").chmod(0o755)
    env = {"HOME": str(home), "PATH": f"{stub}:/usr/bin:/bin:{Path(sys.executable).parent}", "YOLK_HOME": str(home / ".claude" / "yolk")}
    p = subprocess.run(["bash", str(r.HERE / "install_schedule.sh")], env=env, capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    plist = plistlib.loads((home / "Library" / "LaunchAgents" / "com.yolk.agent.plist").read_bytes())
    assert plist["RunAtLoad"] is True and plist["StartInterval"] == 1200 and plist["Label"] == "com.yolk.agent"
    assert plist["EnvironmentVariables"]["YOLK_HOME"] == env["YOLK_HOME"]
    launcher = home / ".claude" / "yolk" / "bin" / "launcher.py"
    assert plist["ProgramArguments"][1] == str(launcher)            # the plist runs the stable launcher
    assert launcher.read_bytes() == (r.HERE / "launcher.py").read_bytes()   # ...and it was copied
    assert "employee_runner.py" not in json.dumps(plist["ProgramArguments"])
    assert "load" in (home / "launchctl.log").read_text()      # went to the stub, not the real launchd
    shutil.rmtree(home, ignore_errors=True)


def check_status_phases(scripts):
    clean_dirs()
    r.STATUS_FILE.unlink(missing_ok=True)
    today = r.summary_today(CFG)
    qdate = (today - timedelta(days=2)).isoformat()
    marker = "STATUS-LEAK-MARKER"
    seen = []

    def api(cfg, method, path, body=None, auth=True):
        if path.startswith("/v1/questions/pending"):
            return 200, {"question_sets": [{"id": "qs_s", "date": qdate, "round": 1, "questions": []}]}
        return (200, {"briefs": []}) if path.startswith("/v1/briefs") else (201, None)

    def claude(prompt, sid, verbose, what, **kw):
        st = r.read_status()
        seen.append((st["phase"], st["date"]))
        return json.dumps({"overview": marker})

    def investigate(qs, profile, tz, verbose):
        st = r.read_status()
        seen.append((st["phase"], st["date"]))
        assert st["open_check_ins"] == [qdate]
        return {"answers": [{"question_id": "q1", "answer": marker}]}

    saved = (r.api, r.run_claude, r.investigate)
    r.api, r.run_claude, r.investigate = api, claude, investigate
    try:
        with fake_collectors(scripts, claude_sessions="ok.py"):
            r.poll_once(CFG, False)
    finally:
        r.api, r.run_claude, r.investigate = saved
    assert ("summarizing", qdate) in seen and ("answering", qdate) in seen, seen
    st = r.read_status()
    assert st["phase"] == "idle" and st["date"] is None and st["last_poll_ended"] and st["last_poll_started"]
    assert st["open_check_ins"] == [] and list(st["answered"]) == [qdate]       # answered: no longer open
    assert set(st) <= {"phase", "date", "since", "last_poll_started", "last_poll_ended",
                       "open_check_ins", "last_error", "answered"}, set(st)
    assert marker not in r.STATUS_FILE.read_text()                               # no summary or answer text
    assert r.STATUS_FILE.stat().st_mode & 0o777 == 0o600
    # a failing run records the class name only
    r.api = lambda *a, **k: (_ for _ in ()).throw(urllib.error.URLError("secret-host-detail"))
    try:
        r.poll_once(CFG, False)
    except urllib.error.URLError:
        pass
    finally:
        r.api = saved[0]
    st = r.read_status()
    assert st["last_error"] == "URLError" and st["phase"] == "idle" and "secret-host" not in r.STATUS_FILE.read_text()


def hold_lock(seconds):
    """A helper process that holds .lock like a running poll; returns once it holds it."""
    import subprocess
    code = ("import fcntl,sys,time\nf=open(sys.argv[1],'w');fcntl.flock(f,fcntl.LOCK_EX);"
            "print('held',flush=True);time.sleep(float(sys.argv[2]))")
    p = subprocess.Popen([sys.executable, "-c", code, str(r.YOLK_HOME / ".lock"), str(seconds)],
                         stdout=subprocess.PIPE, text=True)
    assert p.stdout.readline().strip() == "held"
    return p


def check_status_and_wait():
    clean_dirs()
    r.STATUS_FILE.unlink(missing_ok=True)
    pending = {"sets": []}
    briefs = {"list": []}

    def api(cfg, method, path, body=None, auth=True):
        if path.startswith("/v1/questions/pending"):
            return 200, {"question_sets": pending["sets"]}
        if path.startswith("/v1/briefs"):
            return 200, {"briefs": briefs["list"]}
        return 204, None
    real = r.api
    r.api = api
    old = (r.WAIT_POLL_S, r.WAIT_SYNC_S)
    r.WAIT_POLL_S = r.WAIT_SYNC_S = 0.2
    try:
        # running reflects the lock, and asking never blocks (nor leaves the lock held)
        t0 = time_mod.monotonic()
        assert r.status_report(CFG)["running"] is False
        helper = hold_lock(30)
        try:
            assert r.status_report(CFG)["running"] is True
        finally:
            helper.kill()
            helper.wait()
        assert r.status_report(CFG)["running"] is False and time_mod.monotonic() - t0 < 5
        with open(r.YOLK_HOME / ".lock", "w") as f:  # the poller can still take the lock afterwards
            import fcntl
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)

        # offline: the cached open check-ins
        r.set_status(open_check_ins=["2026-10-01"])
        r.api = lambda *a, **k: (_ for _ in ()).throw(urllib.error.URLError("down"))
        assert r.status_report(CFG)["open_check_ins"] == ["2026-10-01"]
        r.api = api
        r.set_status(open_check_ins=[])
        (r.DAYS_DIR / "2026-10-05.summary.json").write_text("{}")
        (r.BRIEFS_DIR / "2026-10-04.json").write_text("{}")
        s = r.status_report(CFG)
        assert s["reports_ready"] == ["2026-10-05"] and s["briefs"] == ["2026-10-04"] and s["newest_brief"] == "2026-10-04"

        # --wait returns once the lock is released
        helper = hold_lock(1.5)
        t0 = time_mod.monotonic()
        s = r.status_wait(CFG, 30)
        waited = time_mod.monotonic() - t0
        helper.wait()
        assert 1.0 < waited < 10 and s["running"] is False, waited

        # --wait returns at the timeout when a check-in never gets its brief
        pending["sets"] = [{"id": "qs_x", "date": "2026-10-07"}]
        t0 = time_mod.monotonic()
        s = r.status_wait(CFG, 1)
        assert 0.9 < time_mod.monotonic() - t0 < 6 and s["open_check_ins"] == ["2026-10-07"]

        # ...and returns early once the brief arrives
        calls = {"n": 0}

        def api_brief(cfg, method, path, body=None, auth=True):
            if path.startswith("/v1/briefs"):
                calls["n"] += 1
                if calls["n"] >= 3:
                    return 200, {"briefs": [{"date": "2026-10-07", "brief": {"v": 1}}]}
            return api(cfg, method, path, body, auth)
        r.api = api_brief
        t0 = time_mod.monotonic()
        s = r.status_wait(CFG, 30)
        assert time_mod.monotonic() - t0 < 10 and "2026-10-07" in s["briefs"], s
    finally:
        r.api = real
        r.WAIT_POLL_S, r.WAIT_SYNC_S = old
    # status never heals the poller
    seen = []
    real_heal, r.heal_schedule = r.heal_schedule, lambda: seen.append(1)
    real_cfg, r.load_config = r.load_config, lambda: CFG
    real_report = r.status_report
    r.status_report = lambda cfg: {"ok": True}
    try:
        sys.argv = ["employee_runner.py", "status"]
        r.main()
    finally:
        r.heal_schedule, r.load_config, r.status_report = real_heal, real_cfg, real_report
    assert not seen


if __name__ == "__main__":
    real_stdout = sys.stdout
    try:
        with contextlib.redirect_stdout(io.StringIO()):  # the runner's log() prints
            check_contract()
            check_missing_days()
            check_claim()
            check_latest_brief()
            check_sync_briefs_and_heartbeat_glob()
            scripts_dir = check_collectors()
            check_heartbeat_and_answers_flush()
            check_summarize_and_investigate(scripts_dir)
            check_atomic_and_corrupt_outbox()
            check_attempt_cap(scripts_dir)
            check_poll_order(scripts_dir)
            check_claude_flags()
            check_day_boundary()
            check_prune()
            check_heal_schedule()
            check_heal_installs_missing_plist()
            check_install_script_plist()
            check_launcher()
            check_status_phases(scripts_dir)
            check_status_and_wait()
    finally:
        import shutil
        shutil.rmtree(HOME, ignore_errors=True)
    print("check_runner: all checks passed", file=real_stdout)
