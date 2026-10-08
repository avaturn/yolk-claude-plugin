---
description: This plugin coaches you to reach the team goal in the most efficient way. It analyzes your workflows and aligns them to team goals.
argument-hint: "<invite code from your manager, e.g. K7QP-3FXA>"
---

Set up the Yolk Employee Agent on this Mac. This is the ONE approval moment — after this, the agent sends a daily summary of the user's workday and answers the manager's check-ins in the background without nagging.

1. **Explain what the user is approving** (be transparent — this answers work-monitoring questions from their manager's AI):
   - A local agent will read Claude Code session history (`~/.claude/projects`), macOS Screen Time stats, browsing history from every Chromium-family browser it finds (Chrome, Arc, Brave, Edge, Chromium, Vivaldi: domains, page titles, timing — used to reconstruct the workday), local git activity, and any cloud connectors already configured.
   - **A sanitized daily summary of the workday now leaves this Mac, in addition to answers.** Every day the agent reads those sources for the previous workday(s) and sends a business-level summary — what was done, where (tools, docs, clients) and how — so the manager's report starts right away instead of waiting for questions. Personal time leaves only as a total number of minutes, never what it was. It also answers the questions the Manager AI posts about gaps.
   - Only sanitized, business-level text leaves this Mac, over HTTPS to the Yolk server (meeting subjects, doc titles, client names) — never raw code, prompts, shell commands, chats, email/message bodies, URLs, the raw browsing history, or secrets. A deterministic sanitizer enforces this on top of the agent's rules. Every summary and answer sent is kept in `~/.claude/yolk/audit/` (`summary-<date>.sent.json`), and the raw data stays in `~/.claude/yolk/days/` (private to this user).
   - This plugin contains NO manager logic and needs NO API key — summaries and investigations run on the user's own Claude subscription. The manager's daily report about them, and a coach brief built from it, come back to `~/.claude/yolk/` — they read exactly what the manager reads, with `/yolk:report` and `/yolk:brief`. Their device token can only ever read their own questions, reports and briefs.
   - Ask for explicit confirmation before continuing.

2. **Invite**: use `$ARGUMENTS` if present (the code, like `K7QP-3FXA`; case and dashes do not matter), otherwise ask for the invite code their manager sent. If it is a link, ask for the code instead.

3. **Link this Mac**:
   ```
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/employee_runner.py" claim "<invite code>"
   ```
   The script stores the device token itself. Never read, print, or `cat` `~/.claude/yolk/agent.json` — the token must not enter this conversation's transcript. If the script says the invite "isn't valid" or was "already used or expired", tell the user to ask their manager for a new invite and stop.

4. **Show the profile** from `~/.claude/yolk/profile.md`. Tell the user the Manager AI calibrates every judgment against it, and that their manager fixes it if it's wrong.

5. **Verify prerequisites**: `python3 --version` and `claude --version`. The summary and the investigations run the `claude` CLI; if it's missing, help them install Claude Code CLI before step 7.

6. **Full Disk Access (Screen Time)**: macOS lets only programs with Full Disk Access read Screen Time stats; everything else degrades gracefully without it, and a summary says which source could not be read. Two different programs need it (System Settings → Privacy & Security → Full Disk Access, the "+" button, then Cmd+Shift+G to type a path):
   - the terminal (or the Claude Code app) this session runs in — it covers step 7;
   - **the `python3` binary the background poller runs as.** launchd starts that binary directly, not the terminal, so the terminal's permission does not carry over. Get its path with `python3 -c 'import os,sys; print(os.path.realpath(sys.executable))'` and show the user that exact path.

7. **First run, now**: build and send yesterday's summary in the foreground (use the Bash tool's longest timeout, 600000 ms; it collects the data, runs one local Claude summarization, and sends the result, which takes a few minutes):
   ```
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/employee_runner.py" summarize --verbose
   ```
   Then tell the user: yesterday's sanitized summary has been sent; the manager's report starts right away and usually follows within minutes — `/yolk:report` shows it once it arrives. Offer to show what was sent (`~/.claude/yolk/audit/summary-<yesterday>.sent.json`). If a source shows `error` (usually Screen Time without Full Disk Access), say which one. If it says "Cannot reach the Yolk server", the summary is kept and goes out on the next poll; "already in progress" means the background agent is already doing it.

8. **Background schedule**: ask whether to install the background poller (every 20 minutes while the Mac is awake it sends the daily summaries not sent yet, then checks for manager questions). If yes: `bash "${CLAUDE_PLUGIN_ROOT}/scripts/install_schedule.sh"` and repeat the `python3` path it prints for Full Disk Access.

9. Mention: they can run `/yolk:answer` anytime to process questions and summaries immediately, and `/yolk:leave` to unlink this Mac.
