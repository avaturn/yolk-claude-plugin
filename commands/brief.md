---
description: Show your Yolk coach brief (latest, or for a date)
argument-hint: "[YYYY-MM-DD]"
---

1. Fetch new briefs: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/employee_runner.py" sync`. If it fails, say so in one line and continue with what is on disk.
2. Read `~/.claude/yolk/briefs/<date>.json` — the date from `$ARGUMENTS`, otherwise the newest file. If there is none, say no brief has arrived yet (briefs come after the manager's daily run) and stop.
3. Present it in this order: `summary`; `goals`; `priorities`; `day` (each block with minutes and verdict); `reorder`; `misalignment`; `automations`; `offer`. Skip empty or null fields. Keep the brief's own wording and sources — do not add judgments of your own.
