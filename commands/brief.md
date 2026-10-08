---
description: Show your Yolk coach brief (latest, or for a date)
argument-hint: "[YYYY-MM-DD]"
---

1. Fetch new briefs: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/employee_runner.py" sync`. If it fails, say so in one line and continue with what is on disk.
2. If `~/.claude/yolk/briefs/<date>.json` exists — the date from `$ARGUMENTS`, otherwise the newest file — present it in this order: `summary`; `goals`; `priorities`; `day` (each block with minutes and verdict); `reorder`; `misalignment`; `automations`; `offer`. Skip empty or null fields. Keep the brief's own wording and sources — do not add judgments of your own. Stop.
3. Otherwise there is no brief yet. Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/employee_runner.py" status` and say in 1-3 plain lines exactly what is true, from its JSON, for example:
   - "Your activity report for 2026-10-07 is ready (`/yolk:report 2026-10-07`). No check-in is open yet, so there is no brief; the server opens one after its daily run." (`reports_ready` has dates, `open_check_ins` is empty, `running` is false)
   - "The background poller is building your activity report for 2026-10-06 now." (`running` true, `phase` collecting or summarizing, `date`)
   - "A check-in for 2026-10-07 is open; the background poller is answering it." (`open_check_ins`, or `phase` answering)
4. If `running` is true or `open_check_ins` is not empty, also say "this runs in the background; I will tell you when it is ready", then start `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/employee_runner.py" status --wait 1800` with the Bash tool's `run_in_background` and do not poll its output. When it finishes, show the brief if one arrived (as in step 2); otherwise say which activity report(s) became ready (`reports_ready`) and point to `/yolk:report <date>`.
