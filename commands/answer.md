---
description: Check Yolk for Manager-AI questions and answer them now
---

Process pending Yolk check-in questions immediately. The poll answers first, then builds the local summaries of any finished day of the last 7 that has none (they stay on this Mac), so it can take several minutes.

1. Run it with the Bash tool's `run_in_background` (a poll can outlast the 10-minute Bash limit: investigations alone may take up to 25 minutes) and tell the user you will report when it finishes; do not poll its output in a loop:
   ```
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/employee_runner.py" --verbose
   ```
2. Summarize for the user what happened: how many question sets were answered, how many local daily summaries were built, whether the privacy gate redacted anything, and that the exact answers sent are in `~/.claude/yolk/audit/` (`<id>.sent.json`). Summaries are never sent.
3. If it fails: "not linked" or "no longer linked" → `/yolk:setup` with a new invite code from their manager; "Cannot reach the Yolk server" → check the network (unsent answers are kept and go out on the next poll); "already in progress" → the background agent is answering right now.
