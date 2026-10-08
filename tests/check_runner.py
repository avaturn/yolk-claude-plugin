#!/usr/bin/env python3
"""Runnable check of the device runner's daily-summary logic. No pytest, no network, no claude.

    python3 tests/check_runner.py
"""
import contextlib
import io
import json
import os
import plistlib
import sys
import tempfile
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
    script = r.HERE / "employee_runner.py"

    real_root, real_plugins = r.PLUGIN_ROOT, r.INSTALLED_PLUGINS_DIR
    r.INSTALLED_PLUGINS_DIR = tmp / "plugins"           # pretend this copy is the installed plugin
    r.PLUGIN_ROOT = r.INSTALLED_PLUGINS_DIR / "cache" / "yolk"

    def plist(*args, home=None):
        r.LAUNCH_PLIST.write_bytes(plistlib.dumps({
            "Label": "com.yolk.agent", "ProgramArguments": list(args),
            "EnvironmentVariables": {"YOLK_HOME": home or str(r.YOLK_HOME)}}))

    xpc = os.environ.pop("XPC_SERVICE_NAME", None)
    try:
        r.heal_schedule()                                                # no plist: declined poller stays declined
        assert not calls and not r.LAUNCH_PLIST.exists()
        plist("/usr/bin/python3", str(script))                            # current: untouched
        r.heal_schedule()
        assert not calls
        link = tmp / "linked-scripts"
        link.symlink_to(r.HERE)
        plist("/usr/bin/python3", str(link / "employee_runner.py"))       # same file through a symlink: current
        r.heal_schedule()
        assert not calls
        before = agent_log()
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
        assert len(added) == 1 and "re-pointed" in added[0], added          # exactly one log line
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
        assert len(calls) == 3 and "could not re-point" in agent_log()
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
    finally:
        import shutil
        shutil.rmtree(HOME, ignore_errors=True)
    print("check_runner: all checks passed", file=real_stdout)
