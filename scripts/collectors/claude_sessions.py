#!/usr/bin/env python3
"""Collect per-day Claude Code activity stats from ~/.claude/projects transcripts.

Deterministic evidence for the Employee Agent: active hours, query counts,
prompt-quality signals, testing evidence, AI-vs-human contribution signals.
Output is LOCAL ONLY — the privacy gate applies to what leaves the machine,
not to this file.

Usage: claude_sessions.py [--date YYYY-MM-DD] [--exclude-sessions id1,id2]
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECTS_DIR = Path.home() / ".claude" / "projects"
GAP_MINUTES = 15          # bridge gaps shorter than this into one active block
PAD_MINUTES = 2           # padding around isolated events
SHORT_PROMPT_CHARS = 25   # "continue" / "fix" style nudges
LONG_PROMPT_CHARS = 1500  # likely paste-dump (PRD / ticket copy-paste)

TEST_CMD_RE = re.compile(
    r"\b(pytest|npm (run )?test|yarn test|vitest|jest|go test|cargo test|"
    r"curl |http[s]?://localhost|playwright|cypress|make test|python .*test)",
    re.I,
)
BUILD_RUN_RE = re.compile(
    r"\b(npm (run )?(dev|start|build)|yarn (dev|start|build)|vite|next dev|"
    r"docker (compose )?up|uvicorn|flask run|node |python3? (?!.*test))", re.I,
)


def parse_ts(s):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def local_day_bounds(date_str):
    day = datetime.strptime(date_str, "%Y-%m-%d").astimezone()
    start = day.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


def extract_text(content):
    """User message content -> plain text, or None if it's a tool result / non-text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = [b.get("text", "") for b in content
                 if isinstance(b, dict) and b.get("type") == "text"]
        return "\n".join(t for t in texts if t) or None
    return None


def is_real_user_prompt(rec, text):
    if rec.get("isMeta"):
        return False
    if text is None:
        return False
    t = text.strip()
    if not t:
        return False
    # local command echoes / system-injected wrappers
    if t.startswith(("<local-command", "<command-name>", "<command-message>",
                     "<system-reminder>", "Caveat:")):
        return False
    return True


def merge_blocks(timestamps, gap_min=GAP_MINUTES, pad_min=PAD_MINUTES):
    """List of datetimes -> list of (start, end) active blocks."""
    if not timestamps:
        return []
    ts = sorted(timestamps)
    pad = timedelta(minutes=pad_min)
    gap = timedelta(minutes=gap_min)
    blocks = [[ts[0] - pad, ts[0] + pad]]
    for t in ts[1:]:
        if t - blocks[-1][1] <= gap:
            blocks[-1][1] = t + pad
        else:
            blocks.append([t - pad, t + pad])
    return blocks


def classify_prompt(text):
    tags = []
    n = len(text)
    if n <= SHORT_PROMPT_CHARS:
        tags.append("short_nudge")
    if n >= LONG_PROMPT_CHARS:
        tags.append("long_paste_like")
    if "[Request interrupted" in text:
        tags.append("interrupt")
    low = text.lower()
    if any(w in low for w in ("test", "check", "verify", "broken", "bug",
                              "doesn't work", "not working", "wrong", "fix the")):
        tags.append("verification_or_correction")
    if re.search(r"```", text):
        tags.append("contains_code")
    if any(w in low for w in ("step", "first", "then", "plan", "approach",
                              "instead", "constraint", "should", "make sure")):
        tags.append("guidance")
    return tags


def analyze_session(path, day_start, day_end):
    events = []            # all timestamped events for active-time calc
    prompts = []
    ai_edits = 0
    ai_bash = 0
    test_cmds = []
    run_cmds = []
    interrupts = 0
    session_id = path.stem
    model = None

    with open(path, errors="replace") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = parse_ts(rec.get("timestamp"))
            rtype = rec.get("type")
            if ts and day_start <= ts < day_end and rtype in ("user", "assistant"):
                events.append(ts)
            if not ts or not (day_start <= ts < day_end):
                continue

            if rtype == "user":
                msg = rec.get("message", {})
                text = extract_text(msg.get("content"))
                if is_real_user_prompt(rec, text):
                    if "[Request interrupted" in text:
                        interrupts += 1
                        continue
                    prompts.append({
                        "ts": ts.astimezone().strftime("%H:%M"),
                        "chars": len(text),
                        "tags": classify_prompt(text),
                        "preview": text[:180].replace("\n", " "),
                    })
            elif rtype == "assistant":
                msg = rec.get("message", {})
                model = msg.get("model") or model
                for block in msg.get("content") or []:
                    if not isinstance(block, dict) or block.get("type") != "tool_use":
                        continue
                    name = block.get("name", "")
                    if name in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
                        ai_edits += 1
                    elif name == "Bash":
                        ai_bash += 1
                        cmd = (block.get("input") or {}).get("command", "")
                        if TEST_CMD_RE.search(cmd):
                            test_cmds.append(cmd[:120])
                        elif BUILD_RUN_RE.search(cmd):
                            run_cmds.append(cmd[:120])

    if not events:
        return None
    return {
        "session_id": session_id,
        "model": model,
        "events": events,
        "first_activity": min(events).astimezone().strftime("%H:%M"),
        "last_activity": max(events).astimezone().strftime("%H:%M"),
        "user_prompts": prompts,
        "interrupts": interrupts,
        "ai_file_edits": ai_edits,
        "ai_bash_commands": ai_bash,
        "test_commands": test_cmds[:15],
        "run_commands": run_cmds[:10],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--exclude-sessions", default="")
    args = ap.parse_args()
    excluded = {s.strip() for s in args.exclude_sessions.split(",") if s.strip()}
    day_start, day_end = local_day_bounds(args.date)

    projects = {}
    all_events = []
    for proj_dir in sorted(PROJECTS_DIR.iterdir()):
        if not proj_dir.is_dir():
            continue
        sessions = []
        for jf in proj_dir.glob("*.jsonl"):
            if jf.stem in excluded:
                continue
            mtime = datetime.fromtimestamp(jf.stat().st_mtime, tz=timezone.utc)
            if mtime < day_start - timedelta(days=2):
                continue  # can't contain events for the target day
            s = analyze_session(jf, day_start.astimezone(timezone.utc),
                                day_end.astimezone(timezone.utc))
            if s:
                all_events.extend(s.pop("events"))
                sessions.append(s)
        if sessions:
            name = proj_dir.name.replace("-Users-sergeisherman", "~").replace("-", "/")
            total_prompts = sum(len(s["user_prompts"]) for s in sessions)
            projects[name] = {
                "sessions": sessions,
                "total_user_prompts": total_prompts,
            }

    blocks = merge_blocks(all_events)
    active_minutes = round(sum((b[1] - b[0]).total_seconds() for b in blocks) / 60)
    out = {
        "date": args.date,
        "active_claude_minutes": active_minutes,
        "active_hours_formatted": f"{active_minutes // 60}:{active_minutes % 60:02d}",
        "active_blocks": [
            f"{b[0].astimezone().strftime('%H:%M')}–{b[1].astimezone().strftime('%H:%M')}"
            for b in blocks
        ],
        "total_user_prompts": sum(p["total_user_prompts"] for p in projects.values()),
        "projects": projects,
    }
    json.dump(out, sys.stdout, indent=1, ensure_ascii=False)


if __name__ == "__main__":
    main()
