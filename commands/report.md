---
description: Show your own daily activity report (the local summary of a day; latest, or for a date); a manager can also show a team member's report
argument-hint: "[YYYY-MM-DD] | <employee id> [YYYY-MM-DD] (manager)"
---

**Which report:** if the first word of `$ARGUMENTS` is empty or looks like `YYYY-MM-DD`, it is your own report (steps 1-3 below, unchanged). Otherwise it is an employee id (lowercase a name into the id, like `yoav`) with an optional date: a team member's manager report from the Yolk server, manager only. For that, run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/employee_runner.py" member-report "<id>" [--date <date>]`, show its output AS IS (the header line starting "Manager report on", then the markdown) without summarizing unless asked, and relay its message in plain words if it refuses (not a manager, unknown employee, no report). Never read, print or `cat` `~/.claude/yolk/agent.json`, and never save that report anywhere: it is another person's data. Do not do steps 1-3 for it.

Your own report is built on this Mac from your own activity data and is never sent to Yolk or your manager.

1. Read `~/.claude/yolk/days/<date>.summary.json` (the date from `$ARGUMENTS`, else the newest).
2. If there is none, run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/employee_runner.py" status`. If the background job is collecting or summarizing that date (or any date, when none was requested), say so, run `... status --wait 1800` with the Bash tool's `run_in_background` (do not poll it), and show the report when it ends. Otherwise suggest `... summarize --date <date>` (only the last 7 days are summarized automatically; `--date` also works for older days, up to 14), also in the background, and say you will report when it finishes. Stop.
3. Show the `summary` readably, with no judgments: `overview`; active and personal minutes; `blocks` as start–end, kind, what, where, how (confidence if not high); `work_items` (title, outcome, evidence). Name the `sources` that were `error` or `empty`.
