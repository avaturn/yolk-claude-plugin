---
description: One-time HR-for-AI Employee Agent setup — consent, profile, exchange repo, background schedule
argument-hint: "[exchange repo, e.g. org/hrforai-exchange (optional)]"
---

Set up the HR-for-AI Employee Agent on this machine. This is the ONE approval moment — after this, the agent answers the manager's check-ins in the background without nagging.

1. **Explain what the user is approving** (be transparent — this answers work-monitoring questions from their manager's AI):
   - A local agent will read Claude Code session history (`~/.claude/projects`), macOS Screen Time stats, Chrome browsing history (domains, page titles, timing — used to reconstruct the workday), local git activity, and any cloud connectors already configured, to answer questions posted by the Manager AI.
   - Only sanitized, business-level answers leave this machine (meeting subjects, doc titles, client names) — never raw code, chats, email/message bodies, URLs, or secrets. Personal browsing is reported only in aggregate ("~30 min personal"). A deterministic sanitizer enforces this on top of the agent's rules, and every outgoing answer is auditable in `~/.hrforai/audit/` and in the exchange repo.
   - This plugin contains NO manager logic and needs NO API key — investigations run on the user's own Claude subscription. The manager's daily report about them, and a coach brief built from it, are delivered back into their own exchange repo (`reports/`, `briefs/`) — they read exactly what the manager reads, with `yolk brief` / `yolk report`. Nobody else's data is in their repo, and nobody else can see theirs.
   - Ask for explicit confirmation before continuing.

2. **Exchange repo**: use `$ARGUMENTS` if it looks like `owner/repo`, otherwise ask the user which private exchange repo their manager gave them. Verify access with `gh repo view <repo>` (if `gh` is missing or unauthenticated, help them install/`gh auth login` first).

3. **Employee id**: if the repo is named `hrforai-exchange-<id>` (one private repo per employee — the standard team setup), the id is `<id>`: confirm it with the user rather than asking. Otherwise ask for it (agree with the manager; default: their first name, lowercase). It must match the manager's registration exactly.

4. **Profile** (critical for fair, accurate evaluation): ask for their name and role, and what "real work" looks like for that role. **Timezone**: detect with `readlink /etc/localtime` (strip up to `zoneinfo/`), confirm with the user (employees may be in Israel, Italy, Kyrgyzstan…), and let them override with any IANA timezone. Write `~/.hrforai/profile.md`:
   ```
   Name: <name>
   Role: <role>
   Timezone: <IANA tz, e.g. Asia/Jerusalem>

   What "real work" looks like for this role: <1-3 lines>
   Org systems in use: <Slack, Linear, GitHub, Fireflies, Google Workspace, …>
   ```

5. **Write config** `~/.hrforai/agent.json`:
   ```json
   {"exchange_repo": "<owner/repo>", "employee_id": "<id>"}
   ```

6. **Verify prerequisites**: `python3 --version`, `claude --version`, `gh auth status`. Then dry-run: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/employee_runner.py" --verbose` (it should clone the exchange and report "No pending questions").

7. **Background schedule**: ask whether to install the background poller (checks for manager questions every 20 minutes). If yes: `bash "${CLAUDE_PLUGIN_ROOT}/scripts/install_schedule.sh"`.

8. Mention: macOS may need Full Disk Access for the terminal for Screen Time stats (System Settings → Privacy & Security → Full Disk Access); everything else degrades gracefully without it. They can run `/hrforai-agent:answer` anytime to process questions immediately, and audit everything sent in `~/.hrforai/audit/`.
