#!/usr/bin/env python3
"""Yolk — Employee Agent runner (the device side of Yolk Device API v1).

Runs entirely on the employee's Mac. Polls the Yolk server for the Manager AI's
question sets, investigates locally (headless Claude Code with full local
freedom), sanitizes the answers, and posts ONLY the sanitized answers. Also
fetches the employee's own daily reports + coach briefs and sends a heartbeat.
No manager logic and no API keys live on this side.

Data (YOLK_HOME, default ~/.claude/yolk/):
  agent.json            server, device_token, employee_id, timezone, poll_interval_s (0600)
  profile.md            the profile the manager registered (from the claim)
  briefs/<date>.json    coach briefs        reports/<date>.md   daily reports
  outbox/<set id>.json  answers saved but not yet accepted by the server
  audit/                agent.log, <set id>.raw.json (before the gate), <set id>.sent.json

Usage: employee_runner.py [poll] [--verbose]    one poll cycle (what launchd runs)
       employee_runner.py claim <invite link>   link this Mac (/yolk:setup)
       employee_runner.py sync                  fetch briefs + heartbeat
       employee_runner.py leave                 revoke this Mac's device token
"""
import argparse
import fcntl
import json
import os
import platform
import re
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from sanitizer import sanitize_answers  # noqa: E402

PLUGIN_ROOT = HERE.parent
VERSION = json.loads((PLUGIN_ROOT / ".claude-plugin" / "plugin.json").read_text())["version"]
# base URL for bare invite codes: the local server for now; production is https://yolk-plugin.dev.yolk.coach
DEFAULT_SERVER = os.environ.get("YOLK_SERVER", "http://localhost:8080").rstrip("/")
YOLK_HOME = Path(os.environ.get("YOLK_HOME", str(Path.home() / ".claude" / "yolk")))
CONFIG_FILE = YOLK_HOME / "agent.json"
PROFILE_FILE = YOLK_HOME / "profile.md"
BRIEFS_DIR = YOLK_HOME / "briefs"
REPORTS_DIR = YOLK_HOME / "reports"
OUTBOX_DIR = YOLK_HOME / "outbox"
AUDIT_DIR = YOLK_HOME / "audit"
CHARTER = PLUGIN_ROOT / "agents" / "employee-investigator.md"
CLAUDE_TIMEOUT_S = 1500
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
SET_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")  # server ids become local file names
LOOPBACK = ("localhost", "127.0.0.1", "::1")
ANSWER_ITEM_KEYS = ("question_id", "answer", "evidence", "confidence")


class Unlinked(Exception):
    """The server answered 401: this Mac's device token is no longer valid."""


def log(msg):
    line = f"[yolk {datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    with open(AUDIT_DIR / "agent.log", "a") as f:
        f.write(line + "\n")


def machine_timezone():
    try:
        tz = os.readlink("/etc/localtime")
        return tz.split("zoneinfo/")[-1]
    except OSError:
        return datetime.now().astimezone().tzname() or "UTC"


def write_private(path, text):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)


def load_config():
    if not CONFIG_FILE.exists():
        raise SystemExit("This Mac is not linked to Yolk. Run /yolk:setup <invite link>.")
    return json.loads(CONFIG_FILE.read_text())


def load_profile():
    if PROFILE_FILE.exists():
        return PROFILE_FILE.read_text().strip()
    return "No profile on file (role unknown — note this in answers)."


def api(cfg, method, path, body=None, auth=True):
    """One Device API call. Returns (status, parsed JSON or None); raises Unlinked on 401."""
    headers = {"Content-Type": "application/json"}
    if auth:
        headers["Authorization"] = f"Bearer {cfg['device_token']}"
    req = urllib.request.Request(cfg["server"] + path, method=method, headers=headers,
                                 data=None if body is None else json.dumps(body).encode())
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        if e.code == 401 and auth:
            raise Unlinked() from None
        return e.code, None


def claim(link):
    m = re.match(r"\s*(https?://[^/\s]+)/join/([^/?#\s]+)", link)
    server, code = (m.group(1), m.group(2)) if m else (DEFAULT_SERVER, link.strip())
    if not server:
        raise SystemExit("Paste the full invite link from your manager (https://…/join/CODE).")
    u = urllib.parse.urlparse(server)
    if u.scheme != "https" and u.hostname not in LOOPBACK:
        raise SystemExit("Refusing a non-HTTPS server: the device token must not travel unencrypted.")
    device = {"hostname": socket.gethostname().removesuffix(".local"),
              "os": f"macOS {platform.mac_ver()[0]}",
              "timezone": machine_timezone(), "agent_version": VERSION}
    status, body = api({"server": server}, "POST", "/v1/devices/claim",
                       {"invite_code": code, "device": device}, auth=False)
    if status == 404:
        raise SystemExit("This invite isn't valid. Check the link with your manager.")
    if status == 410:
        raise SystemExit("This invite was already used or expired. Ask your manager for a new one.")
    if status != 200:
        raise SystemExit(f"Linking failed (HTTP {status}).")
    emp = body["employee"]
    cfg = {"server": server, "device_token": body["device_token"], "employee_id": emp["id"],
           "name": emp.get("name"), "timezone": emp.get("timezone") or machine_timezone(),
           "poll_interval_s": body.get("poll_interval_s", 1200)}
    write_private(CONFIG_FILE, json.dumps(cfg, indent=1) + "\n")
    PROFILE_FILE.write_text((emp.get("profile_md") or "").strip() + "\n")
    log(f"Linked to {server} as {emp.get('name')} ({emp['id']}), timezone {cfg['timezone']}.")
    sync_briefs(cfg)
    heartbeat(cfg)


def latest_brief():
    return max((p.stem for p in BRIEFS_DIR.glob("*.json") if DATE_RE.fullmatch(p.stem)), default=None)


def sync_briefs(cfg):
    latest = latest_brief()
    _, body = api(cfg, "GET", "/v1/briefs" + (f"?since={latest}" if latest else ""))
    got = 0
    for b in (body or {}).get("briefs", []):
        if not DATE_RE.fullmatch(str(b.get("date"))):
            continue
        (BRIEFS_DIR / f"{b['date']}.json").write_text(
            json.dumps(b.get("brief"), indent=1, ensure_ascii=False) + "\n")
        (REPORTS_DIR / f"{b['date']}.md").write_text((b.get("report_markdown") or "") + "\n")
        got += 1
    if got:
        log(f"Received {got} brief(s) and report(s).")


def heartbeat(cfg, status="ok", error=None):
    sent = max(AUDIT_DIR.glob("*.sent.json"), key=lambda p: p.stat().st_mtime, default=None)
    api(cfg, "POST", "/v1/devices/heartbeat", {
        "agent_version": VERSION, "status": status, "error": error,
        "last_answered_at": datetime.fromtimestamp(sent.stat().st_mtime).astimezone()
                                    .isoformat(timespec="seconds") if sent else None,
        "latest_brief": latest_brief()})


def flush_outbox(cfg):
    """Post every saved answer. 409 means the server already has it — also success."""
    for f in sorted(OUTBOX_DIR.glob("*.json")):
        status, _ = api(cfg, "POST", f"/v1/questions/{f.stem}/answers", json.loads(f.read_text()))
        if status in (201, 409):
            f.replace(AUDIT_DIR / f"{f.stem}.sent.json")
            log(f"Sent answers for {f.stem}" + (" (server already had them)" if status == 409 else ""))
        elif status in (400, 404, 422):  # retrying the same body cannot succeed
            f.replace(AUDIT_DIR / f"{f.stem}.rejected.json")
            log(f"Server rejected {f.stem} (HTTP {status}); kept in audit/{f.stem}.rejected.json.")
        else:
            log(f"Could not send {f.stem} (HTTP {status}); retrying next poll.")


def to_contract(raw):
    """The server refuses unknown keys (422): keep only what Device API v1 accepts."""
    items = []
    for a in raw.get("answers") or []:
        if not isinstance(a, dict) or a.get("question_id") is None:
            continue
        item = {k: str(a[k]) for k in ANSWER_ITEM_KEYS if a.get(k) is not None}
        if "evidence" not in item and a.get("evidence_basis") is not None:
            item["evidence"] = str(a["evidence_basis"])  # the charter's old name; never send both
        item["answer"] = item.get("answer", "")
        confidence = item.get("confidence", "").lower()
        item["confidence"] = confidence if confidence in ("high", "medium", "low") else None
        items.append(item)
    context = raw.get("volunteered_context")
    return {"answers": items, "volunteered_context": str(context) if context else None}


def investigate(payload, profile, tz, verbose):
    """Headless Claude Code investigation. Returns raw answers dict."""
    charter = CHARTER.read_text()
    session_id = str(uuid.uuid4())
    context = ""
    if payload.get("context"):
        context = ("\nContext from the previous round (your earlier sanitized "
                   "answers, provided by the manager):\n"
                   + json.dumps(payload["context"], indent=1, ensure_ascii=False) + "\n")
    prompt = f"""{charter}

## Today's assignment
Investigation date: {payload.get("date")}
Person you represent (calibrate all judgments to this role):
{profile}

TIMEZONE RULE (mandatory): the employee's timezone is **{tz}**, and the machine's
timezone is **{machine_timezone()}**. Convert EVERY timestamp from every source —
calendars, Fireflies, Drive, GitHub, cloud APIs often return UTC or another zone —
to {tz} before reasoning about the day, and state all times in your answers in
{tz}. If a meeting was scheduled in another timezone, say so explicitly.

Plugin directory (run collectors from here): {PLUGIN_ROOT}
Exclude this Claude session id from all analysis (it is the HR run itself,
pass it via --exclude-sessions where supported): {session_id}
{context}
## Questions from the Manager AI
{json.dumps(payload.get("questions", []), indent=1, ensure_ascii=False)}

Investigate now, then output ONLY the answers JSON."""

    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE")}
    env["ANTHROPIC_API_KEY"] = ""  # local subscription auth only
    cmd = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose",
           "--dangerously-skip-permissions", "--session-id", session_id,
           "--max-turns", "60"]
    result_text = None
    with tempfile.TemporaryFile("w+") as err:  # a file, not a pipe: unread stderr cannot block claude
        # own process group: children that inherited stdout must die too, or the read never ends
        proc = subprocess.Popen(cmd, cwd=PLUGIN_ROOT, env=env, text=True, start_new_session=True,
                                stdout=subprocess.PIPE, stderr=err)

        def kill_group():
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

        timer = threading.Timer(CLAUDE_TIMEOUT_S, kill_group)  # a hung claude must not keep .lock
        timer.start()
        try:
            for line in proc.stdout:
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if ev.get("type") == "assistant" and verbose:
                    for b in (ev.get("message") or {}).get("content") or []:
                        if b.get("type") == "tool_use":
                            log(f"  · {b.get('name')} {json.dumps(b.get('input') or {})[:90]}")
                elif ev.get("type") == "result":
                    result_text = ev.get("result", "")
            proc.wait()
        finally:
            timer.cancel()
            kill_group()  # leftover children of a finished or interrupted run
        if proc.returncode != 0 or result_text is None:
            err.seek(0)
            reason = "timed out" if proc.returncode == -signal.SIGKILL else f"rc={proc.returncode}"
            raise RuntimeError(f"Investigation failed ({reason}): {err.read()[-400:]}")
    m = re.search(r"\{[\s\S]*\}", result_text)
    if not m:
        raise RuntimeError(f"No JSON in investigation result: {result_text[:400]}")
    return json.loads(m.group(0))


def poll_once(cfg, verbose):
    flush_outbox(cfg)   # answers a crashed or offline run saved but never sent
    sync_briefs(cfg)    # cheap calls first: a 25-min investigation must not delay them
    heartbeat(cfg)
    _, body = api(cfg, "GET", "/v1/questions/pending")
    sets = (body or {}).get("question_sets", [])
    if not sets:
        log("No pending questions.")
        return 0

    profile = load_profile()
    done, error = 0, None
    for qs in sets:
        sid = str(qs.get("id"))
        if not SET_ID_RE.fullmatch(sid) or (OUTBOX_DIR / f"{sid}.json").exists() \
                or (AUDIT_DIR / f"{sid}.rejected.json").exists():  # re-investigating would repeat the rejection
            continue
        log(f"Investigating {qs.get('date')} round {qs.get('round', 1)} ({sid}) …")
        try:
            raw = investigate(qs, profile, cfg["timezone"], verbose)
        except Exception as e:  # noqa: BLE001 — one failed set must not block the others
            log(f"Investigation failed for {sid}: {e}")
            # class name only: the message can carry unsanitized investigation output
            error = f"investigation failed ({e.__class__.__name__})"
            continue
        (AUDIT_DIR / f"{sid}.raw.json").write_text(json.dumps(raw, indent=1, ensure_ascii=False))
        clean, violations = sanitize_answers(to_contract(raw))
        envelope = {
            "employee_id": cfg["employee_id"],
            "date": qs.get("date"),
            "round": qs.get("round", 1),
            "timezone": cfg["timezone"],
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "agent_version": VERSION,
            "privacy_redactions": violations,
            "answers": clean,
        }
        (OUTBOX_DIR / f"{sid}.json").write_text(json.dumps(envelope, indent=1, ensure_ascii=False))
        log(f"Answered {sid}" + (f" (privacy gate redacted: {violations})" if violations else ""))
        flush_outbox(cfg)
        done += 1
    heartbeat(cfg, "error" if error else "ok", error)
    return done


def leave(cfg):
    try:
        api(cfg, "DELETE", "/v1/devices/me")
    except Unlinked:
        pass  # already revoked on the server
    CONFIG_FILE.unlink()
    log(f"This Mac is unlinked from Yolk. Briefs, reports and the audit trail stay in {YOLK_HOME}.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", nargs="?", default="poll", choices=("poll", "claim", "sync", "leave"))
    ap.add_argument("link", nargs="?", help="invite link (claim only)")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()
    for d in (YOLK_HOME, BRIEFS_DIR, REPORTS_DIR, OUTBOX_DIR, AUDIT_DIR):
        d.mkdir(parents=True, exist_ok=True)
    try:
        if a.action == "claim":
            if not a.link:
                ap.error("claim needs the invite link")
            claim(a.link)
            return
        cfg = load_config()
        if a.action == "leave":
            leave(cfg)
        elif a.action == "sync":
            sync_briefs(cfg)
            heartbeat(cfg)
        else:
            with open(YOLK_HOME / ".lock", "w") as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    log("Another run is already in progress; it posts its answers when done.")
                    return
                n = poll_once(cfg, a.verbose)
            log(f"Done — {n} question set(s) answered.")
    except Unlinked:
        CONFIG_FILE.unlink(missing_ok=True)
        raise SystemExit("This Mac is no longer linked to Yolk (the server revoked its token). "
                         "Run /yolk:setup with a new invite link from your manager.")
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        raise SystemExit(f"Cannot reach the Yolk server ({e}). "
                         f"Unsent answers stay in {OUTBOX_DIR} and go out on the next poll.")


if __name__ == "__main__":
    main()
