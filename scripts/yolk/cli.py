"""The actions (the Command pattern: a table from action name to handler) and their top-level errors.

  poll [--verbose]            one poll cycle (what launchd runs)
  claim <invite code>         link this device (/yolk:setup)
  summarize [--date D | --limit N]  build local daily reports (missing days, or D)
  sync                        fetch briefs + heartbeat
  status [--wait N]           what is happening now (JSON); --wait N waits up to N s for it to finish
  heal                        re-point/install the poller if needed; flag missing Full Disk Access (SessionStart hook)
  context                     collect the company context now (manager role; /yolk:context)
  member-report <id> [--date D]  print one team member's daily report (manager role; /yolk:report <employee>)
  context-roster <date>       the employees waiting for context that day, as JSON (manager role)
  context-file <date> <file>  post that day's context from the manager's own JSON file (manager role)
  context-mode manual|auto    manual: a poll does not collect context; auto (default) does
  manager-consent             record the manager's consent to the context collection (/yolk:setup)
  profile-sources             a digest of the user's own recent Claude Code sessions, as JSON (/yolk:setup; local only)
  profile <file>              send the profile the user approved; it replaces the manager's (/yolk:setup)
  leave                       revoke this device's token

`poll` and `summarize` hold `.lock`. A 401 anywhere means the token is revoked: agent.json is
deleted and the user is told to run /yolk:setup. An unreachable server is a clean exit; saved
answers stay in the outbox for the next poll.
"""
import argparse
import contextlib
import fcntl
import json
import platform
import re
import socket
import sys
import time
import urllib.parse
from datetime import datetime
from pathlib import Path

from . import VERSION, activity, api, config, manager, poll, privacy, schedule, status, store

ACTIONS = ("poll", "claim", "sync", "leave", "summarize", "status", "heal", "manager-consent", "context", "member-report", "context-roster", "context-file", "context-mode", "profile-sources", "profile")


def claim(code):
    code = code.strip()
    if not re.fullmatch(r"[A-Za-z0-9 -]+", code):
        raise SystemExit("Give the invite code from your manager (like K7QP-3FXA), not a link.")
    server = config.DEFAULT_SERVER
    u = urllib.parse.urlparse(server)
    if u.scheme != "https" and u.hostname not in config.LOOPBACK:
        raise SystemExit("Refusing a non-HTTPS server: the device token must not travel unencrypted.")
    device = {"hostname": socket.gethostname().removesuffix(".local"),
              "os": f"macOS {platform.mac_ver()[0]}",
              "timezone": config.machine_timezone(), "agent_version": VERSION}
    code_status, body = api.claim(server, code, device)
    if code_status == 404:
        raise SystemExit("This invite isn't valid. Check the code with your manager.")
    if code_status == 410:
        raise SystemExit("This invite was already used or expired. Ask your manager for a new one.")
    if code_status != 200:
        raise SystemExit(f"Linking failed (HTTP {code_status}).")
    emp = body["employee"]
    cfg = {"server": server, "device_token": body["device_token"], "employee_id": emp["id"],
           "role": body.get("role") if body.get("role") in ("employee", "manager") else "employee",
           "name": emp.get("name"), "timezone": emp.get("timezone") or config.machine_timezone()}
    store.write_private(config.CONFIG_FILE, json.dumps(cfg, indent=1) + "\n")
    config.PROFILE_FILE.write_text((emp.get("profile_md") or "").strip() + "\n")
    store.log(f"Linked to {server} as {emp.get('name')} ({emp['id']}), role {cfg['role']}, timezone {cfg['timezone']}.")
    poll.sync_briefs(cfg)
    poll.heartbeat(cfg)


def leave(cfg):
    try:
        api.revoke(cfg)
    except api.Unlinked:
        pass  # already revoked on the server
    config.CONFIG_FILE.unlink()
    store.log(f"This Mac is unlinked from Yolk. Briefs, old reports, local summaries and the audit trail stay in {config.YOLK_HOME}.")


def manager_consent(cfg):
    """The manager said yes to the context collection (setup step 4b): only now does a poll collect."""
    if cfg.get("role") != "manager":
        raise SystemExit("This device is not a manager's.")
    store.write_private(config.CONFIG_FILE, json.dumps({**cfg, "context_consent": True}, indent=1) + "\n")
    store.log("Manager consent recorded: the plugin will collect company context once per date.")


def context_now(cfg, verbose):
    """The `context` action: one context step now, like a poll's, and what came of it as JSON."""
    if cfg.get("role") != "manager" or not cfg.get("context_consent"):
        raise SystemExit("Only a linked manager who consented in /yolk:setup can collect context.")
    out = {}
    with store.tracked_run():
        error = manager.context_step(cfg, verbose, out)
        store.set_status(last_error=error)
    print(json.dumps(out, indent=1))


def _manager_only(cfg):
    if cfg.get("role") != "manager":
        raise SystemExit("Only a linked manager can do this.")


def _pending_date(cfg, date):
    _manager_only(cfg)
    if not config.parse_date(date or ""):
        raise SystemExit("Give the date as YYYY-MM-DD.")
    return manager.pending_item(cfg, date)


def context_roster(cfg, date):
    """The employees the server wants context about on `date`, as JSON (for a manual context file)."""
    item = _pending_date(cfg, date)
    print(json.dumps({"date": date, "employees": [
        {k: e.get(k) for k in ("id", "name", "role")} for e in item.get("employees") or []]}, indent=1))


def context_file(cfg, date, path):
    """Post `date`'s context from the manager's own JSON file: no connectors, so no consent step.
    The file has the body shape minus generated_at, agent_version and privacy_redactions."""
    item = _pending_date(cfg, date)
    try:
        p = Path(path or "")
        if p.stat().st_size > 1_000_000:
            raise SystemExit("The context file is too large.")
        raw = json.loads(p.read_text())
    except (OSError, ValueError):
        raise SystemExit("Give a readable JSON file with the context.") from None
    if not isinstance(raw, dict):
        raise SystemExit("The context file must hold a JSON object.")
    ids = {str(e.get("id")) for e in item.get("employees") or [] if isinstance(e, dict)}
    shaped = manager.shape(raw, ids)
    status = manager.post(cfg, date, raw, ids)
    print(json.dumps({"date": date, "status": status or "failed", "goals": len(shaped["goals"]),
                      "facts": {e["employee_id"]: len(e["facts"]) for e in shaped["employees"]}}, indent=1))


def context_mode(cfg, mode):
    """`manual`: a poll does not collect context (only /yolk:context does); `auto` (default): it does."""
    _manager_only(cfg)
    if mode not in ("manual", "auto"):
        raise SystemExit("Give manual or auto.")
    store.write_private(config.CONFIG_FILE, json.dumps({**cfg, "context_mode": mode}, indent=1) + "\n")
    store.log(f"Context collection is now {mode}.")


def _first_prompt(path, claude_sessions):
    """The first real user prompt of one session transcript, or None."""
    with open(path, errors="replace") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if not isinstance(rec, dict) or rec.get("type") != "user":
                continue
            content = (rec.get("message") or {}).get("content")
            if isinstance(content, list):  # VS Code's <ide_opened_file> / <ide_selection> blocks carry code
                content = [b for b in content if not (isinstance(b, dict) and str(b.get("text", "")).lstrip().startswith("<ide_"))]
            text = claude_sessions.extract_text(content)
            if claude_sessions.is_real_user_prompt(rec, text) and "[Request interrupted" not in text:
                return text
    return None


def profile_sources():
    """The `profile-sources` action (setup step 4): what the user worked on in Claude Code in the last
    PROFILE_SOURCE_DAYS days, to draft their profile from. Nothing is sent. The plugin's own headless
    runs are left out like the summarizer leaves them out: their project path is the plugin directory
    (this one, or any version under ~/.claude/plugins)."""
    sys.path.insert(0, str(config.COLLECTORS_DIR))  # the collectors import _common as a top-level module
    import _common
    import claude_sessions

    def encoded(p):  # how Claude Code names a project dir
        return re.sub(r"[^A-Za-z0-9]", "-", str(p))

    own, plugins = encoded(config.PLUGIN_ROOT), encoded(config.INSTALLED_PLUGINS_DIR) + "-"
    cutoff = time.time() - config.PROFILE_SOURCE_DAYS * 86400
    projects = []  # (dir, [(mtime, transcript)] newest first)
    try:
        dirs = [d for d in claude_sessions.PROJECTS_DIR.iterdir() if d.is_dir()]
    except OSError:
        dirs = []
    for d in dirs:
        if d.name == own or d.name.startswith(plugins):
            continue
        sessions = []
        for f in d.glob("*.jsonl"):
            with contextlib.suppress(OSError):
                if (mtime := f.stat().st_mtime) >= cutoff:
                    sessions.append((mtime, f))
        if sessions:
            projects.append((d, sorted(sessions, key=lambda s: -s[0])))
    projects.sort(key=lambda p: -p[1][0][0])
    out = []
    for d, sessions in projects[:config.PROFILE_MAX_PROJECTS]:
        prompts = []
        for _, f in sessions:
            if len(prompts) == config.PROFILE_EXCERPTS:
                break
            with contextlib.suppress(OSError):
                text = _first_prompt(f, claude_sessions)
                if text:  # the gate sees the full text, then it is cut (see privacy.py)
                    prompts.append(" ".join(privacy.sanitize_text(text)[0].split())[:config.PROFILE_EXCERPT_CHARS])
        out.append({"project": _common.tilde(_common.decode_project_dir(d.name)), "sessions": len(sessions),
                    "last_active": datetime.fromtimestamp(sessions[0][0]).date().isoformat(),
                    "first_prompts": prompts})
    total = sum(len(s) for _, s in projects)
    return {"days": config.PROFILE_SOURCE_DAYS, "sessions": total,
            "thin": total < config.PROFILE_THIN_SESSIONS, "projects": out}


def send_profile(cfg, path):
    """The `profile` action (setup step 4): send the profile the user approved. On the server it replaces
    the one the manager wrote; profile.md changes only once the server has stored it. Nothing is sent
    that the privacy gate would change: the user approved this exact text."""
    def rejected(reason):  # setup fixes the draft and asks again
        print(json.dumps({"status": "rejected", "reason": reason}))
        raise SystemExit(1)

    try:
        text = Path(path or "").read_text()
    except (OSError, ValueError):
        raise SystemExit("Give a readable markdown file with the profile.") from None
    text, violations = privacy.sanitize_text(text)
    text = text.strip()
    # the gate cuts at 6000 chars, below the server's 20000: refuse rather than send a cut profile
    if not text or "oversized_answer_truncated" in violations:
        raise SystemExit("The profile must not be empty or longer than 6000 characters.")
    if violations:
        rejected("privacy gate: " + ", ".join(sorted(set(violations))))
    code, body = api.put_profile(cfg, text)
    if code == 422:
        rejected((body if isinstance(body, dict) else {}).get("detail"))
    if code != 204:
        raise SystemExit(f"Could not send the profile (HTTP {code}).")
    store.write_private(config.PROFILE_FILE, text + "\n")
    store.write_private(config.AUDIT_DIR / "profile.sent.md", text + "\n")
    store.log(f"Sent the user's own profile ({len(text)} chars); it replaces the manager's.", echo=False)
    print(json.dumps({"status": "sent"}))


def member_report(cfg, employee_id, date):
    """The `member-report` action (manager only): print one team member's report. Nothing is saved:
    it is another person's data."""
    if cfg.get("role") != "manager":
        raise SystemExit("Only a linked manager can read team members' reports.")
    employee_id = (employee_id or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", employee_id):
        raise SystemExit("Give the employee id, like yoav.")
    if date and not config.parse_date(date):
        raise SystemExit("Give the date as YYYY-MM-DD.")
    code, body = api.member_report(cfg, employee_id, date)
    body = body if isinstance(body, dict) else {}
    if code == 404:
        raise SystemExit(f"Not available: {body.get('detail') or 'not found'}.")
    if code != 200:
        raise SystemExit(f"Could not read the report (HTTP {code}).")
    print(f"Manager report on {body.get('name')} ({body.get('role')}) — {body.get('date')} — {body.get('state')}")
    if body.get("report_markdown"):
        print()
        print(body["report_markdown"])
    elif body.get("report_missing"):
        print("The stored report has expired.")
    else:
        print(f"No report yet: the check-in is {body.get('state')}.")


def sync(cfg):
    poll.sync_briefs(cfg)
    poll.heartbeat(cfg)


def locked(run):
    """Run `run()` holding `.lock`, or say another run is in progress."""
    with open(config.YOLK_HOME / ".lock", "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            store.log("Another run is already in progress; it posts its answers when done.")
            return
        run()


def poll_cycle(cfg, args):
    n = poll.poll_once(cfg, args.verbose)
    if n or args.verbose:
        store.log(f"Done — {n} question set(s) answered.")


# action -> handler(cfg, args). Handlers are looked up by name at call time (a lambda, not the
# function object), so a test can patch `cli.leave`.
COMMANDS = {
    "leave": lambda cfg, a: leave(cfg),
    "manager-consent": lambda cfg, a: manager_consent(cfg),
    "context": lambda cfg, a: locked(lambda: context_now(cfg, a.verbose)),
    "member-report": lambda cfg, a: member_report(cfg, a.code, a.date),
    "context-roster": lambda cfg, a: context_roster(cfg, a.code),
    "context-file": lambda cfg, a: locked(lambda: context_file(cfg, a.code, a.extra)),
    "context-mode": lambda cfg, a: context_mode(cfg, a.code),
    "profile": lambda cfg, a: send_profile(cfg, a.code),
    "sync": lambda cfg, a: sync(cfg),
    "status": lambda cfg, a: print(json.dumps(status.wait(cfg, a.wait) if a.wait else status.report(cfg), indent=1)),
    "summarize": lambda cfg, a: locked(lambda: activity.summarize_cli(cfg, a.date, a.limit, a.verbose)),
    "poll": lambda cfg, a: locked(lambda: poll_cycle(cfg, a)),
}


def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", nargs="?", default="poll", choices=ACTIONS)
    ap.add_argument("code", nargs="?", help="invite code (claim), employee id (member-report), date (context-*), "
                                            "mode (context-mode) or markdown file (profile)")
    ap.add_argument("extra", nargs="?", help="context-file only: the JSON file")
    ap.add_argument("--date", help="YYYY-MM-DD (summarize, member-report only; default: every missing day)")
    ap.add_argument("--limit", type=int, default=config.SUMMARY_BACKFILL_DAYS,
                    help="summarize only: build at most N missing days, newest first")
    ap.add_argument("--wait", type=int, default=0, help="status only: wait up to N seconds")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()
    if a.date and a.action not in ("summarize", "member-report"):
        ap.error("--date only applies to summarize and member-report")
    needs = {"claim": "the invite code", "member-report": "the employee id", "context-roster": "the date",
             "context-file": "the date and the JSON file", "context-mode": "manual or auto",
             "profile": "the markdown file"}
    if a.action in needs and not a.code or a.action == "context-file" and not a.extra:
        ap.error(f"{a.action} needs {needs[a.action]}")
    return a


def main():
    a = parse_args()
    if a.action == "heal":
        # SessionStart hook: no network, no dirs, no poll. Its stdout reaches the session, so it prints only
        # what it changed and the Full Disk Access hint
        with contextlib.suppress(Exception):
            schedule.heal_schedule()
            hint = schedule.full_disk_access_hint()
            if hint:
                print(hint)
        return
    if a.action == "profile-sources":  # local and read-only: no link, no dirs, no heal
        print(json.dumps(profile_sources(), indent=1, ensure_ascii=False))
        return
    store.ensure_dirs()
    # status is read-only; a heal could unload a running poll. manager-consent and profile run in setup
    # before its consent steps: a heal there would install the poller early
    if a.action not in ("leave", "status", "manager-consent", "profile"):
        schedule.heal_schedule()
    try:
        if a.action == "claim":
            claim(a.code)
            return
        COMMANDS[a.action](config.load_config(), a)
    except api.Unlinked:
        config.CONFIG_FILE.unlink(missing_ok=True)
        raise SystemExit("This Mac is no longer linked to Yolk (the server revoked its token). "
                         "Run /yolk:setup with a new invite code from your manager.")
    except api.NETWORK_ERRORS as e:
        raise SystemExit(f"Cannot reach the Yolk server ({e}). Unsent answers stay in "
                         f"{config.OUTBOX_DIR} and go out on the next poll.")
