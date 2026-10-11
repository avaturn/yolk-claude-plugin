---
description: Manager only: collect the company context now, instead of at the next poll, and show what was sent.
argument-hint: ""
---

Collect the company context now (manager role).

1. Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/employee_runner.py" context` with the Bash tool's `run_in_background`: one collector run can take up to 20 minutes. Never read, print or `cat` `~/.claude/yolk/agent.json` (it holds the device token). If it says only a linked manager who consented can collect context, relay that and stop.
2. When it finishes, report its JSON `status` in one or two lines (`sent`, `already_received`, `rejected`, `failed`, `cooling` = a failed run is waiting before the next try, `nothing_pending`) and the date in `collected`.
3. If `audit_file` is set (a `context-<date>.sent.json`), read it and show a short summary: the goals (text and source), and per employee the number of facts and their sources. Show the fact texts only if the user asks. The data is the manager's own.
4. If `pending` lists more dates than the one collected, say that `/yolk:context` can be run again for the next one.
