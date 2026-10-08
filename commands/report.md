---
description: Show your own daily activity report (the local summary of a day; latest, or for a date)
argument-hint: "[YYYY-MM-DD]"
---

This is the summary built on this Mac from your own activity data. It is never sent to Yolk or your manager.

1. Pick `~/.claude/yolk/days/<date>.summary.json`: the date from `$ARGUMENTS`, otherwise the newest file matching `YYYY-MM-DD.summary.json`.
2. If there is none, say so and suggest `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/employee_runner.py" summarize --date <date>` (only days within the last 7 are summarized automatically). Run it with the Bash tool's `run_in_background` (it can outlast the 10-minute Bash limit) and report when it finishes. Stop.
3. Render the file's `summary` readably, without adding judgments: the `overview`; active and personal minutes; the `blocks` as a list of `start–end`, kind, what, where, how (and confidence when it is not high); then the `work_items` (title, outcome, evidence). Say which `sources` were `error` or `empty`, since the summary could not see them.
