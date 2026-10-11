"""The actions (the Command pattern: a table from action name to handler) and their top-level errors.

  poll [--verbose]            one poll cycle (what launchd runs)
  claim <invite code>         link this device (/yolk:setup)
  summarize [--date D | --limit N]  build local daily reports (missing days, or D)
  sync                        fetch briefs + heartbeat
  status [--wait N]           what is happening now (JSON); --wait N waits up to N s for it to finish
  heal                        re-point/install the poller if needed; flag missing Full Disk Access (SessionStart hook)
  manager-consent             record the manager's consent to the context collection (/yolk:setup)
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
import urllib.parse

from . import VERSION, activity, api, config, poll, schedule, status, store

ACTIONS = ("poll", "claim", "sync", "leave", "summarize", "status", "heal", "manager-consent")


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
           "name": emp.get("name"), "timezone": emp.get("timezone") or config.machine_timezone(),
           "poll_interval_s": body.get("poll_interval_s", 1200)}
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
    store.log(f"Done — {n} question set(s) answered.")


# action -> handler(cfg, args). Handlers are looked up by name at call time (a lambda, not the
# function object), so a test can patch `cli.leave`.
COMMANDS = {
    "leave": lambda cfg, a: leave(cfg),
    "manager-consent": lambda cfg, a: manager_consent(cfg),
    "sync": lambda cfg, a: sync(cfg),
    "status": lambda cfg, a: print(json.dumps(status.wait(cfg, a.wait) if a.wait else status.report(cfg), indent=1)),
    "summarize": lambda cfg, a: locked(lambda: activity.summarize_cli(cfg, a.date, a.limit, a.verbose)),
    "poll": lambda cfg, a: locked(lambda: poll_cycle(cfg, a)),
}


def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", nargs="?", default="poll", choices=ACTIONS)
    ap.add_argument("code", nargs="?", help="invite code (claim only)")
    ap.add_argument("--date", help="YYYY-MM-DD (summarize only; default: every missing day)")
    ap.add_argument("--limit", type=int, default=config.SUMMARY_BACKFILL_DAYS,
                    help="summarize only: build at most N missing days, newest first")
    ap.add_argument("--wait", type=int, default=0, help="status only: wait up to N seconds")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()
    if a.date and a.action != "summarize":
        ap.error("--date only applies to summarize")
    if a.action == "claim" and not a.code:
        ap.error("claim needs the invite code")
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
    store.ensure_dirs()
    if a.action not in ("leave", "status", "manager-consent"):  # status is read-only; a heal could unload a running poll
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
