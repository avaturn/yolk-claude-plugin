"""manager role: claim stores the role, the context step, its audit files, attempts, readonly flags."""
import json
import os
import tempfile
from pathlib import Path

from support import CFG, REAL_RUN_CLAUDE, clean_dirs
from yolk import api, claude, cli, config, manager, poll, store

MGR = {**CFG, "role": "manager", "context_consent": True}
EMP = [{"id": "matt", "name": "Matt Rossi", "role": "AE", "timezone": "Europe/Rome", "profile_md": "gh: mrossi"}]
GOOD = {"goals": [{"id": "g1", "text": "Ship v0.1", "source": "linear:project/X", "confidence": "high"}],
        "employees": [{"employee_id": "matt", "facts": [{"text": "Merged PR 42", "source": "github:o/r#42", "confidence": "bogus"}]},
                      {"employee_id": "ghost", "facts": [{"text": "x", "source": "y"}]}],
        "sources_checked": ["github"], "extra": 1}


class Stub:
    """A stub API: pending dates, a post status per call, and a fake collector."""
    def __init__(self, dates, post_status=201, reply=None):
        self.dates, self.post_status, self.reply = dates, post_status, reply if reply is not None else json.dumps(GOOD)
        self.events, self.posts, self.collected = [], [], []

    def request(self, cfg, method, path, body=None, auth=True):
        self.events.append(path)
        if path == "/v1/context/pending":
            return 200, {"dates": [{"date": d, "employees": EMP} for d in self.dates]}
        if path.startswith("/v1/context/"):
            self.posts.append((path.rsplit("/", 1)[1], body))
            return self.post_status, None
        if path.startswith("/v1/questions/pending"):
            return 200, {"question_sets": []}
        return (200, {"briefs": []}) if path.startswith("/v1/briefs") else (204, None)

    def claude(self, prompt, sid, verbose, what, **kw):
        assert kw.get("readonly") and not kw.get("toolless"), kw
        self.collected.append(prompt)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply

    def __enter__(self):
        self.saved = (api.request, claude.run_claude)
        api.request, claude.run_claude = self.request, self.claude
        return self

    def __exit__(self, *a):
        api.request, claude.run_claude = self.saved


def check_claim_role():
    for given, want in (({"role": "manager"}, "manager"), ({}, "employee"), ({"role": "weird"}, "employee")):
        clean_dirs()
        body = {"device_token": "t", "employee": {"id": "e1", "name": "T", "profile_md": "p"}, **given}
        real, api.request = api.request, lambda cfg, m, path, b=None, auth=True: (200, body if "claim" in path else {})
        try:
            cli.claim("K7QP-3FXA")
        finally:
            api.request = real
        assert json.loads(config.CONFIG_FILE.read_text())["role"] == want
        assert f"role {want}" in (config.AUDIT_DIR / "agent.log").read_text()
        config.CONFIG_FILE.unlink()


def check_context_step():
    clean_dirs()
    with Stub(["2026-10-09", "2026-10-08"]) as s:
        assert manager.context_step(MGR, False) is None
        assert [d for d, _ in s.posts] == ["2026-10-09"] and len(s.collected) == 1        # one run per poll, newest first
        assert manager.context_step(MGR, False) is None                                    # the next poll: the next date
    assert [d for d, _ in s.posts] == ["2026-10-09", "2026-10-08"] and len(s.collected) == 2
    body = s.posts[0][1]
    assert set(body) == {"generated_at", "agent_version", "privacy_redactions", "goals", "employees", "sources_checked"}
    assert body["employees"] == [{"employee_id": "matt", "facts": [
        {"text": "Merged PR 42", "source": "github:o/r#42", "confidence": None}]}]       # ghost dropped, bad enum -> None
    assert body["agent_version"] == "1.8.0"
    for d in ("2026-10-08", "2026-10-09"):
        assert (config.AUDIT_DIR / f"context-{d}.raw.json").exists() and (config.AUDIT_DIR / f"context-{d}.sent.json").exists()
    assert not list(config.OUTBOX_DIR.glob("*"))                                           # nothing in outbox/
    with Stub(["2026-10-08"]) as s:                                                        # sent: never collected again
        manager.context_step(MGR, False)
    assert not s.collected and not s.posts
    # 409 counts as sent
    clean_dirs()
    with Stub(["2026-10-08"], post_status=409) as s:
        manager.context_step(MGR, False)
    assert (config.AUDIT_DIR / "context-2026-10-08.sent.json").exists()


def check_gate_and_clip():
    clean_dirs()
    raw = {"goals": [{"id": "g1", "text": "G " * 300, "source": "s"}],
           "employees": [{"employee_id": "matt", "facts": [{"text": "key sk-ant-abcdefghij1234 used", "source": "s"}] + [
               {"text": f"f{i}", "source": "s"} for i in range(30)]}]}
    with Stub(["2026-10-08"], reply=json.dumps(raw)) as s:
        manager.context_step(MGR, False)
    body = s.posts[0][1]
    assert "sk-ant" not in json.dumps(body) and body["privacy_redactions"] == ["secret_pattern"]
    assert len(body["goals"][0]["text"]) == 200 and len(body["employees"][0]["facts"]) == 15
    assert "sk-ant" in (config.AUDIT_DIR / "context-2026-10-08.raw.json").read_text()      # raw is pre-gate
    assert "sk-ant" not in (config.AUDIT_DIR / "context-2026-10-08.sent.json").read_text()


def check_rejected_and_attempts():
    clean_dirs()
    with Stub(["2026-10-08"], post_status=422) as s:
        manager.context_step(MGR, False)
        manager.context_step(MGR, False)
    assert len(s.posts) == 1 and len(s.collected) == 1                                     # never retried
    assert (config.AUDIT_DIR / "context-2026-10-08.rejected.json").exists()
    # a transient post failure re-posts the saved raw, without collecting again
    clean_dirs()
    with Stub(["2026-10-08"], post_status=503) as s:
        manager.context_step(MGR, False)
        s.post_status = 201
        manager.context_step(MGR, False)
    assert len(s.collected) == 1 and len(s.posts) == 2 and (config.AUDIT_DIR / "context-2026-10-08.sent.json").exists()
    # a failing collector: 3 attempts, then given up
    clean_dirs()
    with Stub(["2026-10-08"], reply="no json here") as s:
        for n in (1, 2, 3, 4, 5):
            err = manager.context_step(MGR, False)
        assert len(s.collected) == 3 and not s.posts, s.collected
    assert err is None and (config.AUDIT_DIR / "context-2026-10-08.failed.json").exists()
    # a claude failure (logged out): counted, then cools down
    clean_dirs()
    with Stub(["2026-10-08"], reply=claude.ClaudeFailed("rc=1")) as s:
        assert manager.context_step(MGR, False) == "context failed (ClaudeFailed)"
        manager.context_step(MGR, False)
    assert len(s.collected) == 1                                                           # cooling down
    assert not (config.DAYS_DIR / "2026-10-08.context.attempts").exists()                  # and not counted
    # a re-post of a saved raw body is not a run: it does not stop this poll's one collection
    clean_dirs()
    (config.AUDIT_DIR / "context-2026-10-09.raw.json").write_text(json.dumps(GOOD))
    with Stub(["2026-10-09", "2026-10-08"]) as s:
        manager.context_step(MGR, False)
    assert [d for d, _ in s.posts] == ["2026-10-09", "2026-10-08"] and len(s.collected) == 1


def check_poll_roles():
    """Only a manager runs the context step, and before the answers."""
    for role, consent, expect in (("manager", True, True), ("manager", False, False), ("manager", None, False),
                                  ("employee", True, False), (None, None, False)):
        clean_dirs()
        cfg = {k: v for k, v in {**CFG, "role": role, "context_consent": consent}.items() if v is not None}
        with Stub(["2026-10-08"]) as s:
            poll.poll_once(cfg, False)
        assert ("/v1/context/pending" in s.events) == expect and bool(s.posts) == expect, (role, s.events)
        if expect:
            assert s.events.index("/v1/context/2026-10-08") < s.events.index("/v1/questions/pending"), s.events
        assert not list(config.OUTBOX_DIR.glob("*"))
    # context-*.sent.json is not "last answered"
    (config.AUDIT_DIR / "context-2026-10-08.sent.json").write_text("{}")
    sent = []
    real, api.request = api.request, lambda cfg, m, path, body=None, auth=True: (sent.append(body), (204, None))[1]
    try:
        poll.heartbeat(CFG)
    finally:
        api.request = real
    assert sent[0]["last_answered_at"] is None


def check_manager_consent():
    clean_dirs()
    cfg = {**CFG, "role": "manager"}
    store.write_private(config.CONFIG_FILE, json.dumps(cfg))
    cli.manager_consent(cfg)
    assert json.loads(config.CONFIG_FILE.read_text())["context_consent"] is True
    config.CONFIG_FILE.unlink()
    try:
        cli.manager_consent(CFG)
        raise AssertionError("employee consent accepted")
    except SystemExit:
        pass


def check_readonly_flags():
    bindir = Path(tempfile.mkdtemp(prefix="yolk-bin-"))
    out = bindir / "argv.json"
    fake = bindir / "claude"
    fake.write_text("#!/usr/bin/env python3\nimport json,sys\nopen(%r,'w').write(json.dumps([sys.argv[1:], sys.stdin.read()]))\n"
                    "print(json.dumps({'type':'result','result':'{}'}))\n" % str(out))
    fake.chmod(0o755)
    old_path, os.environ["PATH"] = os.environ["PATH"], f"{bindir}:{os.environ['PATH']}"
    try:
        REAL_RUN_CLAUDE("PROMPT", "11111111-1111-1111-1111-111111111111", False, "Context", readonly=True)
        argv, stdin = json.loads(out.read_text())
    finally:
        os.environ["PATH"] = old_path
    assert "--allowedTools" in argv and argv[argv.index("--permission-mode") + 1] == "dontAsk"
    assert "--dangerously-skip-permissions" not in argv and "--strict-mcp-config" not in argv
    assert argv[argv.index("--tools") + 1] == "" and "PROMPT" not in argv and stdin == "PROMPT"
    tools = argv[argv.index("--allowedTools") + 1].split(",")
    assert "mcp__claude_ai_GithubMCP__list_commits" in tools
    settings = json.loads(argv[argv.index("--settings") + 1])
    assert settings["disableAllHooks"] is False                                              # a user setting must not switch the gate off
    hook = settings["hooks"]["PreToolUse"][0]
    assert hook["matcher"] == "*" and hook["hooks"][0]["timeout"] == 10
    assert str(config.SCRIPTS_DIR / "readonly_gate.py") in hook["hooks"][0]["command"]
    assert not [t for t in tools if "slack_read" in t], tools                               # can read private channels
    reads = ("list", "get", "search", "read", "fetch", "workspace")  # the verb of every name; no writes
    verb = lambda t: t.rsplit("__", 1)[1].replace("slack_", "").replace("fireflies_", "").replace("notion-", "").replace("pull_request_", "").replace("issue_", "")  # noqa: E731
    assert not [t for t in tools if not verb(t).startswith(reads)], tools


def check_readonly_gate():
    import subprocess
    import sys
    gate = str(config.SCRIPTS_DIR / "readonly_gate.py")

    def run(stdin):
        return subprocess.run([sys.executable, "-I", gate], input=stdin, text=True, capture_output=True).returncode
    assert run(json.dumps({"tool_name": "mcp__claude_ai_Linear__list_teams"})) == 0
    assert run(json.dumps({"tool_name": "mcp__grafana__user_info"})) == 2
    assert run(json.dumps({"tool_name": "mcp__claude_ai_Slack__slack_send_message"})) == 2
    assert run("not json") == 2 and run("") == 2 and run("{}") == 2


def check_context_action():
    import contextlib
    import io
    import types
    from support import hold_lock

    def run(cfg, stub):
        """cli.COMMANDS['context'] with agent.json cfg; returns (parsed stdout JSON or None, log text)."""
        buf = io.StringIO()
        with stub, contextlib.redirect_stdout(buf):
            cli.COMMANDS["context"](cfg, types.SimpleNamespace(verbose=False))
        out = buf.getvalue()
        return (json.loads(out[out.index("{"):out.rindex("}") + 1]) if "{" in out else None), out

    for cfg in ({**CFG, "role": "employee", "context_consent": True}, {**CFG, "role": "manager"}, CFG):
        clean_dirs()
        s = Stub(["2026-10-08"])
        try:
            run(cfg, s)
            raise AssertionError("not refused")
        except SystemExit as e:
            assert "Only a linked manager who consented" in str(e)
        assert not s.events and not s.collected
    clean_dirs()
    out, _ = run(MGR, Stub([]))
    assert out == {"pending": [], "collected": None, "status": "nothing_pending", "audit_file": None}, out
    clean_dirs()
    s = Stub(["2026-10-07", "2026-10-08"])
    out, _ = run(MGR, s)
    assert out["pending"] == ["2026-10-08", "2026-10-07"] and out["collected"] == "2026-10-08" and out["status"] == "sent"
    assert out["audit_file"] == str(config.AUDIT_DIR / "context-2026-10-08.sent.json") and len(s.collected) == 1
    assert not list(config.OUTBOX_DIR.glob("*"))
    clean_dirs()
    out, _ = run(MGR, Stub(["2026-10-08"], post_status=409))
    assert out["status"] == "already_received" and out["audit_file"].endswith("context-2026-10-08.sent.json")
    clean_dirs()
    out, _ = run(MGR, Stub(["2026-10-08"], post_status=422))
    assert out["status"] == "rejected" and out["audit_file"].endswith("context-2026-10-08.rejected.json")
    clean_dirs()
    out, _ = run(MGR, Stub(["2026-10-08"], reply="no json"))
    assert out["status"] == "failed" and out["collected"] == "2026-10-08" and out["audit_file"] is None
    clean_dirs()
    s = Stub(["2026-10-08"], reply=claude.ClaudeFailed("rc=1"))
    run(MGR, s)
    out, _ = run(MGR, s)                                                                   # now cooling: no second run
    assert out["status"] == "cooling" and out["collected"] is None and len(s.collected) == 1
    # a running poll holds .lock: the action says so and does nothing
    clean_dirs()
    p = hold_lock(5)
    try:
        s = Stub(["2026-10-08"])
        out, _ = run(MGR, s)
    finally:
        p.kill()
    assert out is None and not s.events and not s.collected
