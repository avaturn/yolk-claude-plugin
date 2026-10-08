---
description: Link this Mac to Yolk — consent, claim your invite, background schedule
argument-hint: "<invite code from your manager, e.g. K7QP-3FXA>"
---

Set up the Yolk Employee Agent on this Mac. This is the ONE approval moment — after this, the agent answers the manager's check-ins in the background without nagging.

1. **Explain what the user is approving** (be transparent — this answers work-monitoring questions from their manager's AI):
   - A local agent will read Claude Code session history (`~/.claude/projects`), macOS Screen Time stats, Chrome browsing history (domains, page titles, timing — used to reconstruct the workday), local git activity, and any cloud connectors already configured, to answer questions posted by the Manager AI.
   - Only sanitized, business-level answers leave this Mac, over HTTPS to the Yolk server (meeting subjects, doc titles, client names) — never raw code, chats, email/message bodies, URLs, or secrets. Personal browsing is reported only in aggregate ("~30 min personal"). A deterministic sanitizer enforces this on top of the agent's rules, and every answer sent is kept in `~/.claude/yolk/audit/`.
   - This plugin contains NO manager logic and needs NO API key — investigations run on the user's own Claude subscription. The manager's daily report about them, and a coach brief built from it, come back to `~/.claude/yolk/` — they read exactly what the manager reads, with `/yolk:report` and `/yolk:brief`. Their device token can only ever read their own questions, reports and briefs.
   - Ask for explicit confirmation before continuing.

2. **Invite**: use `$ARGUMENTS` if present (the code, like `K7QP-3FXA`; case and dashes do not matter), otherwise ask for the invite code their manager sent. If it is a link, ask for the code instead.

3. **Link this Mac**:
   ```
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/employee_runner.py" claim "<invite code>"
   ```
   The script stores the device token itself. Never read, print, or `cat` `~/.claude/yolk/agent.json` — the token must not enter this conversation's transcript. If the script says the invite "isn't valid" or was "already used or expired", tell the user to ask their manager for a new invite and stop.

4. **Show the profile** from `~/.claude/yolk/profile.md`. Tell the user the Manager AI calibrates every judgment against it, and that their manager fixes it if it's wrong.

5. **Verify prerequisites**: `python3 --version` and `claude --version`. The background agent needs the `claude` CLI on PATH; if it's missing, help them install Claude Code CLI before step 6.

6. **Background schedule**: ask whether to install the background poller (checks for manager questions every 20 minutes while the Mac is awake). If yes: `bash "${CLAUDE_PLUGIN_ROOT}/scripts/install_schedule.sh"`.

7. Mention: macOS may need Full Disk Access for the terminal for Screen Time stats (System Settings → Privacy & Security → Full Disk Access); everything else degrades gracefully without it. They can run `/yolk:answer` anytime to process questions immediately, and `/yolk:leave` to unlink this Mac.
