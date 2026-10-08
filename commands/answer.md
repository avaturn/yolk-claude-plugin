---
description: Check Yolk for Manager-AI questions and answer them now
---

Process pending Yolk check-in questions immediately. A poll also builds and sends any daily summary of a past workday that has not been sent yet, so it can take several minutes.

1. Run in the foreground with progress, using the Bash tool's longest timeout (600000 ms):
   ```
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/employee_runner.py" --verbose
   ```
2. Summarize for the user what happened: how many daily summaries were built and sent, how many question sets were answered, whether the privacy gate redacted anything, and that the exact payloads sent are in `~/.claude/yolk/audit/` (`summary-<date>.sent.json`, `<id>.sent.json`).
3. If it fails: "not linked" or "no longer linked" → `/yolk:setup` with a new invite code from their manager; "Cannot reach the Yolk server" → check the network (unsent answers are kept and go out on the next poll); "already in progress" → the background agent is answering right now.
