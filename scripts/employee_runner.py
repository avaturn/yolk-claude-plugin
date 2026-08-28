#!/usr/bin/env python3
"""HR for AI — Employee Agent runner.

Runs entirely on the employee's machine. Checks the org's exchange repo for
question files from the Manager AI, investigates locally (headless Claude Code
with full local freedom), sanitizes the answers, and pushes ONLY the sanitized
answers back. No manager prompts, no reports, and no API keys live on this side.

Exchange layout (a private git repo, e.g. org/hrforai-exchange):
  inbox/<employee_id>/<name>.json   questions from the manager
  outbox/<employee_id>/<name>.json  sanitized answers (written by this runner)

Config:  ~/.hrforai/agent.json   {"exchange_repo": "org/hrforai-exchange", "employee_id": "sergei"}
Profile: ~/.hrforai/profile.md   (name, role, Timezone: <IANA tz>)

Usage: employee_runner.py [--once] [--verbose]
"""
import argparse
import json
import os
import re
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from sanitizer import sanitize_answers  # noqa: E402

PLUGIN_ROOT = HERE.parent
HRFORAI_DIR = Path.home() / ".hrforai"
CONFIG_FILE = HRFORAI_DIR / "agent.json"
PROFILE_FILE = HRFORAI_DIR / "profile.md"
EXCHANGE_DIR = HRFORAI_DIR / "exchange"
AUDIT_DIR = HRFORAI_DIR / "audit"
CHARTER = PLUGIN_ROOT / "agents" / "employee-investigator.md"
CLAUDE_TIMEOUT_S = 1500


def log(msg):
    line = f"[hrforai-agent {datetime.now().strftime('%H:%M:%S')}] {msg}"
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


def load_config():
    if not CONFIG_FILE.exists():
        raise SystemExit("Not configured. Run /hrforai-agent:setup first "
                         f"(missing {CONFIG_FILE}).")
    return json.loads(CONFIG_FILE.read_text())


def load_profile():
    if PROFILE_FILE.exists():
        return PROFILE_FILE.read_text().strip()
    return "No profile on file (role unknown — note this in answers)."


def profile_timezone(profile_text):
    m = re.search(r"^Timezone:\s*(\S+)", profile_text, re.M)
    return m.group(1) if m else machine_timezone()


def git(args, check=True):
    r = subprocess.run(["git", "-C", str(EXCHANGE_DIR)] + args,
                       capture_output=True, text=True, timeout=120)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {r.stderr[:300]}")
    return r.stdout.strip()


def ensure_exchange(repo):
    if not (EXCHANGE_DIR / ".git").exists():
        EXCHANGE_DIR.parent.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(["gh", "repo", "clone", repo, str(EXCHANGE_DIR)],
                           capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            raise RuntimeError(f"Cannot clone exchange repo {repo}: {r.stderr[:300]}")
    git(["pull", "--rebase", "--quiet"])


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
    proc = subprocess.Popen(cmd, cwd=PLUGIN_ROOT, env=env, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    result_text = None
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
    proc.wait(timeout=CLAUDE_TIMEOUT_S)
    if proc.returncode != 0 or result_text is None:
        raise RuntimeError(f"Investigation failed (rc={proc.returncode}): "
                           f"{proc.stderr.read()[-400:]}")
    m = re.search(r"\{[\s\S]*\}", result_text)
    if not m:
        raise RuntimeError(f"No JSON in investigation result: {result_text[:400]}")
    return json.loads(m.group(0))


def process_pending(cfg, verbose):
    me = cfg["employee_id"]
    ensure_exchange(cfg["exchange_repo"])
    inbox = EXCHANGE_DIR / "inbox" / me
    outbox = EXCHANGE_DIR / "outbox" / me
    outbox.mkdir(parents=True, exist_ok=True)
    pending = [f for f in sorted(inbox.glob("*.json"))
               if not (outbox / f.name).exists()] if inbox.exists() else []
    if not pending:
        log("No pending questions.")
        return 0

    profile = load_profile()
    tz = profile_timezone(profile)
    done = 0
    for f in pending:
        log(f"Processing {f.name} …")
        payload = json.loads(f.read_text())
        raw = investigate(payload, profile, tz, verbose)
        AUDIT_DIR.mkdir(parents=True, exist_ok=True)
        (AUDIT_DIR / f"{f.stem}.raw.json").write_text(
            json.dumps(raw, indent=1, ensure_ascii=False))
        clean, violations = sanitize_answers(raw)
        envelope = {
            "employee_id": me,
            "date": payload.get("date"),
            "round": payload.get("round", 1),
            "timezone": tz,
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "privacy_redactions": violations,
            "answers": clean,
        }
        (outbox / f.name).write_text(json.dumps(envelope, indent=1, ensure_ascii=False))
        git(["add", str(outbox / f.name)])
        git(["commit", "-q", "-m", f"answers: {me}/{f.name}"])
        for attempt in (1, 2):
            try:
                git(["pull", "--rebase", "--quiet"])
                git(["push", "-q"])
                break
            except RuntimeError:
                if attempt == 2:
                    raise
        log(f"Answered {f.name}"
            + (f" (privacy gate redacted: {violations})" if violations else ""))
        done += 1
    return done


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="(default behavior)")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    cfg = load_config()
    n = process_pending(cfg, args.verbose)
    log(f"Done — {n} request(s) answered.")


if __name__ == "__main__":
    main()
