---
description: This plugin coaches you to reach the team goal in the most efficient way. It analyzes your workflows and aligns them to team goals.
argument-hint: "<invite code from your manager, e.g. K7QP-3FXA>"
---

Set up the Yolk Employee Agent on this Mac. This is the ONE approval moment — after this, the agent answers the manager's check-ins in the background without nagging.

1. **Explain what the user is approving** (be transparent — this answers work-monitoring questions from their manager's AI):
   - A local agent will read Claude Code session history (`~/.claude/projects`), macOS Screen Time stats, browsing history from every Chromium-family browser it finds (Chrome, Arc, Brave, Edge, Chromium, Vivaldi: domains, page titles, timing — used to reconstruct the workday), local git activity, and any cloud connectors already configured.
   - **Raw activity data and daily summaries are never sent to Yolk or the manager; they are processed by Claude under the user's own account.** The agent reads those sources, has Claude (the user's own Claude Code subscription) turn each finished workday into a private summary (what, where, how), and keeps it in `~/.claude/yolk/days/`. It uses that summary to answer the questions the Manager AI posts about the user's day.
   - **Only sanitized answers to the Manager AI's questions leave this Mac**, over HTTPS to the Yolk server, at business level (meeting subjects, doc titles, client names) — never raw code, prompts, shell commands, chats, email/message bodies, URLs, the raw browsing history, or secrets. Personal activity is reported only in aggregate. A deterministic sanitizer enforces this on top of the agent's rules, and every answer sent is kept in `~/.claude/yolk/audit/` (`<id>.sent.json`). The raw data in `days/` is private to this user and deleted after 14 days.
   - This plugin contains NO manager logic and needs NO API key — summaries and investigations run on the user's own Claude subscription (Claude Opus 5.5). The manager sees the user's sanitized answers and a team digest (the user never sees the digest). The user sees their own daily activity report — the local summary, with `/yolk:report` — and a coach brief built from their answers, with `/yolk:brief`. Their device token can only ever read their own questions and briefs.
   - Ask for explicit confirmation before continuing.

2. **Invite**: use `$ARGUMENTS` if present (the code, like `K7QP-3FXA`; case and dashes do not matter), otherwise ask for the invite code their manager sent. If it is a link, ask for the code instead.

3. **Link this Mac**:
   ```
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/employee_runner.py" claim "<invite code>"
   ```
   The script stores the device token itself. Never read, print, or `cat` `~/.claude/yolk/agent.json` — the token must not enter this conversation's transcript. If the script says the invite "isn't valid" or was "already used or expired", tell the user to ask their manager for a new invite and stop.

4. **Show the profile** from `~/.claude/yolk/profile.md`. Tell the user the Manager AI calibrates every judgment against it, and that their manager fixes it if it's wrong.

5. **Verify prerequisites**: `python3 --version` and `claude --version`. The summaries and the investigations run the `claude` CLI; if it's missing, help them install Claude Code CLI before step 7.

6. **Full Disk Access (Screen Time)**: macOS lets only programs with Full Disk Access read Screen Time stats; everything else degrades gracefully without it, and a summary notes which source could not be read. Two different programs need it (System Settings → Privacy & Security → Full Disk Access, the "+" button, then Cmd+Shift+G to type a path):
   - the terminal (or the Claude Code app) this session runs in — it covers step 7;
   - **the `python3` binary the background poller runs as.** launchd starts that binary directly, not the terminal, so the terminal's permission does not carry over. Get its path with `python3 -c 'import os,sys; print(os.path.realpath(sys.executable))'` and show the user that exact path.

7. **First run, now**: build yesterday's local summary. Run it with the Bash tool's `run_in_background` (it can outlast the 10-minute Bash limit) and report when it finishes; it collects the data and runs one local Claude summarization:
   ```
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/employee_runner.py" summarize --limit 1 --verbose
   ```
   Then tell the user: yesterday's summary was built and stays on this Mac (`~/.claude/yolk/days/<yesterday>.summary.json`; offer to show it); the background poller fills in the rest of the last 7 days over the next polls, and answers the manager's questions. If a source shows `error` (usually Screen Time without Full Disk Access), say which one. "already in progress" means the background agent is already doing it.

8. **Background schedule**: the server may have started a check-in for yesterday when the Mac was linked, so questions may arrive within minutes. Ask whether to install the background poller (every 20 minutes while the Mac is awake it answers manager questions, then builds the local summaries still missing for the last 7 days). If yes: `bash "${CLAUDE_PLUGIN_ROOT}/scripts/install_schedule.sh"` and repeat the `python3` path it prints for Full Disk Access. If they decline the poller, tell them to run `/yolk:answer` within 3 hours, or yesterday is reported as unanswered, and to run it daily after that.

9. Mention: they can run `/yolk:answer` anytime to process questions immediately, and `/yolk:leave` to unlink this Mac.
