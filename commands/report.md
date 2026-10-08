---
description: Show the daily report your manager received about you (latest, or for a date)
argument-hint: "[YYYY-MM-DD]"
---

1. Fetch new reports: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/employee_runner.py" sync`. If it fails, say so in one line and continue with what is on disk.
2. Read `~/.claude/yolk/reports/<date>.md` — the date from `$ARGUMENTS`, otherwise the newest file. If there is none, say no report has arrived yet and stop.
3. Show the report verbatim. It is exactly what the manager sees — do not summarize, soften, or comment on it unless the user asks.
