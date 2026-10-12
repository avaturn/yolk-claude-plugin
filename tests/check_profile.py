"""cli: profile-sources and profile (the user's own profile, /yolk:setup step 4)."""
import contextlib
import io
import json
import os
import re
import sys
import tempfile
import time
import types
from pathlib import Path

from support import CFG, clean_dirs
from yolk import api, cli, config, schedule


def check_send_profile():
    def act(path, status, body=None):
        calls = []
        real, api.request = api.request, lambda cfg, method, p, body_=None, auth=True: calls.append((method, p, body_)) or (status, body)
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                cli.COMMANDS["profile"](CFG, types.SimpleNamespace(code=str(path)))
            err = None
        except SystemExit as e:
            err = e.code
        finally:
            api.request = real
        return err, (json.loads(buf.getvalue()) if buf.getvalue().strip() else None), calls

    clean_dirs()
    sent_audit = config.AUDIT_DIR / "profile.sent.md"
    f = config.YOLK_HOME / "profile-draft.md"
    draft = "## Role and responsibilities\nBackend lead for billing.\n\n## Handles\nGitHub: dana-k\n"
    f.write_text("\n" + draft + "\n\n")
    err, out, calls = act(f, 204)
    assert err is None and out == {"status": "sent"}, (err, out)
    assert [(m, p) for m, p, _ in calls] == [("PUT", "/v1/profile")]
    assert calls[0][2]["profile_md"] == draft.strip()
    assert config.PROFILE_FILE.read_text() == draft == sent_audit.read_text()

    # 422, or text the privacy gate would change: the reason is reported, exit 1, nothing local changes
    config.PROFILE_FILE.write_text("manager's profile\n")
    sent_audit.unlink()
    err, out, calls = act(f, 422, {"detail": "contains a URL"})
    assert err == 1 and out == {"status": "rejected", "reason": "contains a URL"} and len(calls) == 1, (err, out)
    f.write_text(draft + "Deploys with token: sk-ant-abcdefghij1234\n")
    err, out, calls = act(f, 204)
    assert err == 1 and out == {"status": "rejected", "reason": "privacy gate: secret_pattern"} and not calls, (err, out)
    assert config.PROFILE_FILE.read_text() == "manager's profile\n" and not sent_audit.exists()

    # refused before anything is sent: empty, too long for the privacy gate, missing
    for text in (" \n\n", "word " * 1300):
        f.write_text(text)
        err, out, calls = act(f, 204)
        assert "must not be empty or longer" in err and not calls and out is None, err
    err, _, calls = act(config.YOLK_HOME / "nope.md", 204)
    assert "readable markdown file" in err and not calls
    assert config.PROFILE_FILE.read_text() == "manager's profile\n"

    # setup runs `profile` before the consent steps: it must not heal (install) the poller
    f.write_text(draft)
    seen = []
    real = (schedule.heal_schedule, config.load_config, api.request, sys.argv)
    schedule.heal_schedule, config.load_config = lambda: seen.append(1), lambda: CFG
    api.request = lambda cfg, method, p, body_=None, auth=True: (204, None)
    try:
        sys.argv = ["employee_runner.py", "profile", str(f)]
        cli.main()
    finally:
        schedule.heal_schedule, config.load_config, api.request, sys.argv = real
    assert not seen and config.PROFILE_FILE.read_text() == draft
    f.unlink()
    config.PROFILE_FILE.unlink()


def check_profile_sources():
    sys.path.insert(0, str(config.COLLECTORS_DIR))
    import claude_sessions
    real = claude_sessions.PROJECTS_DIR
    root = Path(tempfile.mkdtemp(prefix="yolk-projects-"))
    claude_sessions.PROJECTS_DIR = root
    now = time.time()

    def enc(p):
        return re.sub(r"[^A-Za-z0-9]", "-", str(p))

    def project(name, n, age_s, prompt="Plan the Q4 pricing page"):
        d = root / name
        d.mkdir()
        for i in range(n):
            s = d / f"s{i}.jsonl"
            s.write_text("\n".join(json.dumps(r) for r in (
                {"type": "user", "isMeta": True, "message": {"content": "<command-name>/yolk:setup</command-name>"}},
                {"type": "user", "message": {"content": [{"type": "tool_result", "content": "x"}]}},
                {"type": "assistant", "message": {"content": [{"type": "text", "text": "hi"}]}},
                {"type": "user", "message": {"content": f"{prompt} {i}"}})) + "\n")
            os.utime(s, (now - age_s - i, now - age_s - i))

    try:
        project("-yolkcheck-acme", 6, 3600, "Deploy it, token: sk-ant-abcdefghij1234 " + "x" * 300)
        project("-yolkcheck-beta", 1, 60)
        project("-yolkcheck-old", 4, 40 * 86400)                                     # outside 30 days
        project(enc(config.PLUGIN_ROOT), 3, 60)                                       # this plugin's own runs
        project(enc(config.INSTALLED_PLUGINS_DIR / "cache" / "yolk" / "yolk" / "1.6.1"), 3, 60)  # an older version's
        ide = root / "-yolkcheck-ide"  # VS Code puts the open file / selection (code) in its own text blocks
        ide.mkdir()
        (ide / "s.jsonl").write_text(json.dumps({"type": "user", "message": {"content": [
            {"type": "text", "text": "<ide_selection>def charge(card): ...</ide_selection>"},
            {"type": "text", "text": "Refactor the billing flow"}]}}) + "\n")
        os.utime(ide / "s.jsonl", (now - 7200, now - 7200))
        out = cli.profile_sources()
        assert out["sessions"] == 8 and out["thin"] is True and out["days"] == 30, out
        assert [p["project"] for p in out["projects"]] == ["/yolkcheck/beta", "/yolkcheck/acme", "/yolkcheck/ide"], out["projects"]
        assert out["projects"][2]["first_prompts"] == ["Refactor the billing flow"], out["projects"][2]
        acme = out["projects"][1]
        assert acme["sessions"] == 6 and len(acme["first_prompts"]) == 5 and acme["last_active"] == time.strftime("%Y-%m-%d", time.localtime(now - 3600))
        assert all(len(p) <= 200 and p.startswith("Deploy it, [secret redacted]") for p in acme["first_prompts"]), acme
        assert out["projects"][0]["first_prompts"] == ["Plan the Q4 pricing page 0"]
        assert "sk-ant" not in json.dumps(out)
        project("-yolkcheck-gamma", 2, 120)
        out = cli.profile_sources()
        assert out["sessions"] == 10 and out["thin"] is False and len(out["projects"]) == 4
    finally:
        claude_sessions.PROJECTS_DIR = real
        import shutil
        shutil.rmtree(root, ignore_errors=True)
