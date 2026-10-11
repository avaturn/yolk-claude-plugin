---
description: Show your Yolk coach brief (latest, or for a date)
argument-hint: "[YYYY-MM-DD]"
---

1. Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/employee_runner.py" sync` (if it fails, say so in one line and use what is on disk).
2. If `~/.claude/yolk/briefs/<date>.json` exists (the date from `$ARGUMENTS`, else the newest), show it in this order: `summary`, `goals`, `priorities`, `day` (minutes and verdict per block), `reorder`, `misalignment`, `automations`, `offer`. Skip empty fields; keep its wording; add no judgments. Stop.
3. Otherwise run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/employee_runner.py" status` and say in 1-3 plain lines what is true: which reports are ready (`reports_ready`, `/yolk:report <date>`), what the background job is doing (`running`, `phase`, `date`), whether a check-in is open (`open_check_ins`), else that the server opens one after its daily run.
4. If `running` is true or a check-in is open, say it runs in the background and you will tell them when it is ready. Run `... status --wait 1800` with the Bash tool's `run_in_background`; do not poll it. When it ends, show the brief if one arrived, else say which reports became ready.
