---
description: Manager only: collect the company context now, switch it to manual or auto, or post a date's context from your own markdown file
argument-hint: "[manual | auto | <YYYY-MM-DD> <path to a markdown file>]"
---

Company context (manager role). Look at `$ARGUMENTS`. Never read, print or `cat` `~/.claude/yolk/agent.json` (it holds the device token). Run every command as `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/employee_runner.py" <action> ...`.

## No arguments: collect now

1. Run `context` with the Bash tool's `run_in_background`: one collector run can take up to 20 minutes. If it says only a linked manager who consented can collect context, relay that and stop.
2. When it finishes, report its JSON `status` in one or two lines (`sent`, `already_received`, `rejected`, `failed`, `cooling` = a failed run is waiting before the next try, `nothing_pending`) and the date in `collected`.
3. If `audit_file` is set (a `context-<date>.sent.json`), read it and show a short summary: the goals (text and source), and per employee the number of facts and their sources. Show the fact texts only if the user asks. The data is the manager's own.
4. If `pending` lists more dates than the one collected, say that `/yolk:context` can be run again for the next one.

## `manual` or `auto`

Run `context-mode <manual|auto>`, then say what it means: in `auto` (the default) the background job collects context itself with the manager's connectors; in `manual` it does not, and context comes only from `/yolk:context` (run now) or `/yolk:context <date> <file>`. To redo a day: `/yolk:context manual`, then `just redo <id> <date> fresh` on the server, then `/yolk:context <date> context.md`.

## `<YYYY-MM-DD> <path>`: post a date's context from your own file

1. Run `context-roster <date>`. If it refuses (the date is not waiting for context), relay its message and stop.
2. Read the markdown file at `<path>`.
3. Draft the body from the file ONLY: up to 6 goals `{"id", "text" (<=200 chars), "source", "confidence": "high|medium|low"}` and, per roster employee the file mentions, up to 15 facts `{"text" (<=300 chars), "source", "confidence"}`. Map names to roster ids; ignore people who are not on the roster. The `source` is `manual: <file name>`, or a more specific source the file itself cites. Never invent anything, quote no person, business level only. The body is `{"goals": [...], "employees": [{"employee_id": "...", "facts": [...]}], "sources_checked": ["manual"]}`.
4. Write it to a temp file under `~/.claude/yolk/` (for example `context-draft.json`).
5. Show a short summary of what will be sent: the goals, and the number of facts per employee.
6. Run `context-file <date> <tmpfile>`, report its JSON `status` in a line, and delete the temp file.
