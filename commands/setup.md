---
description: This plugin coaches you to reach the team goal in the most efficient way. It analyzes your workflows and aligns them to team goals.
argument-hint: "<invite code from your manager, e.g. K7QP-3FXA>"
---

Set up Yolk on this Mac. This is the one approval moment.

1. **Say what the user approves, then ask for explicit confirmation.** Be transparent: this answers work-monitoring questions from the manager's AI about how the user worked.
   - Yolk reads Claude Code sessions (`~/.claude/projects`), Screen Time, git, any cloud connectors already set up, and browser history (domains, page titles, timing) from Chrome, Arc, Brave, Edge, Chromium and Vivaldi. Questions can be about any past day the manager picks, including days before setup; the last 7 days are also summarized automatically.
   - **After this approval a background job answers the manager's check-ins automatically.** The user does not review each answer before it is sent; every answer sent is kept in `~/.claude/yolk/audit/` (`<id>.sent.json`) to read afterwards.
   - **Only sanitized answers to the questions from the manager's AI leave this Mac**, over HTTPS to the Yolk server, at business level (meeting subjects, document titles, client names). The agent is instructed never to include raw code or file contents, verbatim prompts or chats, email or message bodies, full URLs or secrets; it may paraphrase, judge how the user works with AI, and name work pages by title or domain. A deterministic filter also removes code blocks and secret-shaped strings before anything is sent. Personal activity is reported only as a total.
   - **Raw activity data and daily reports stay on this Mac.** They are never sent to Yolk or the manager; Claude processes them under the user's own account (Opus 5.5, no API key). Each finished day becomes a private summary in `~/.claude/yolk/days/`, which the agent uses to answer the questions. Raw data is deleted after 14 days.
   - The manager sees the questions, the sanitized answers, the same coach brief the user gets (with its verdict on each part of the day), when each answer arrived and when this device last checked in, and a team digest the user never sees. The user sees their own daily report (`/yolk:report`) and a coach brief built from their answers (`/yolk:brief`). The device token can only read the user's own questions and briefs. The plugin contains no manager logic.
   - The background job runs on this Mac at every login, right after the Mac wakes, and every 20 minutes, to do all of the above. `/yolk:leave` removes it.
2. **Invite code:** use `$ARGUMENTS`, else ask for the code the manager sent (not a link).
3. **Link:** `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/employee_runner.py" claim "<invite code>"`. Never read, print or `cat` `~/.claude/yolk/agent.json` (it holds the device token). If the invite is "not valid" or "already used or expired", tell the user to ask their manager for a new one and stop.
4. **Show `~/.claude/yolk/profile.md`:** the Manager AI calibrates to it; the manager fixes it if it is wrong.
5. **Check `python3 --version` and `claude --version`;** help install the Claude Code CLI if it is missing.
6. **Full Disk Access (for Screen Time; the rest works without it):** System Settings → Privacy & Security → Full Disk Access → "+" (Cmd+Shift+G to type a path), for the terminal or Claude Code app, and for the `python3` the background job runs as. Show its path: `python3 -c 'import os,sys; print(os.path.realpath(sys.executable))'`.
7. **Start the background job:** `bash "${CLAUDE_PLUGIN_ROOT}/scripts/install_schedule.sh"`. It starts at once; the first run builds yesterday's report and answers the check-in the server may have opened.
8. **Auto-update, once:** `/plugin` → Marketplaces → `yolk` → Enable auto-update (skip if the organization manages it).
9. Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/employee_runner.py" status --wait 1800` with the Bash tool's `run_in_background`; do not poll it. Tell the user the session will say when the first report (and the brief, usually within 20-40 minutes) is ready; then point to `/yolk:report <date>` for each date in `reports_ready`, and `/yolk:brief` if a brief arrived. Progress: `~/.claude/yolk/audit/agent.log`.
