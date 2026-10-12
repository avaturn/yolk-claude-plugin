# Yolk

Yolk is a Claude Code plugin for your team's daily check-in (work monitoring). Each day your manager's
AI asks a few questions about how you worked. A background job answers them automatically from
activity data on your computer. It runs every 2 minutes. Yolk also shows you a daily report and a
coach brief.

You need macOS, Claude Code, and an invite code from your manager.

## Privacy

- **Sent to Yolk:** answers to your manager's questions, at business level (meeting subjects, document
  titles, client names), and the work profile you approve during setup.
- **Not sent to Yolk or your manager:** your raw activity data (Claude Code sessions, Screen Time, git,
  browser history, the tools you connected to Claude) and your daily reports. Claude processes them
  under your own Claude account. Raw data is deleted after 14 days.
- Yolk is instructed not to include raw code, file contents, verbatim prompts or chats, message
  bodies, full URLs or secrets. It may paraphrase your work. A filter also removes longer code blocks
  and secret-like text before sending. Personal activity is reported only as a total.
- You do not review each answer before it is sent. A copy of everything sent is in
  `~/.claude/yolk/audit/`.
- Your manager sees the questions, your answers, your work profile, your coach brief, when your
  computer last checked in, and a daily report the manager's AI writes from your answers.

`/yolk:setup` shows the full details and asks for your consent.

## Install

1. In Claude Code, run:
   ```
   /plugin marketplace add avaturn/yolk-claude-plugin
   /plugin install yolk@avaturn
   ```
2. Open a new session.
3. Run `/yolk:setup <invite code>`.
4. Optional: turn on updates in `/plugin` → Marketplaces → `avaturn` → Enable auto-update.

## Commands

| Command | What it does |
|---|---|
| `/yolk:setup <code>` | Link this computer and start Yolk |
| `/yolk:brief [date]` | Show your coach brief |
| `/yolk:report [date]` | Show your daily report |
| `/yolk:leave` | Unlink this computer and stop Yolk |

## Manager role

A manager invite code adds the manager role. Setup asks for a separate consent for it. If you decline,
setup unlinks the computer.

The manager role collects company context once a day. The manager's AI turns this context into
extra questions for the team, and team members can read these questions.

- **Reads (read-only):** the manager's connected tools: Linear, GitHub, public Slack channels, team
  meetings, Notion, Google Drive, monday.com, HubSpot.
- **Sends:** company goals and work facts about each team member, with sources, through the same
  privacy filter. It does not quote people.
- **Skips:** direct messages and private channels. It is also instructed to skip 1:1, board and
  investor meetings, HR material, and individual deal amounts.

| Command | What it does |
|---|---|
| `/yolk:context` | Collect the context now |
| `/yolk:context manual` | Stop automatic collection (`auto` starts it again) |
| `/yolk:context <date> <file.md>` | Send the context for a date from your own file |
| `/yolk:report <member> [date]` | Show the manager's AI report on a team member |

## Uninstall

Run `/yolk:leave`, then `claude plugin uninstall yolk@avaturn` in a terminal. Your local data stays in
`~/.claude/yolk/` until you delete that folder.

## For admins

To add the plugin on every computer, use
[managed settings](https://code.claude.com/docs/en/plugins/org):

```json
{
  "extraKnownMarketplaces": {
    "avaturn": {
      "source": { "source": "github", "repo": "avaturn/yolk-claude-plugin" },
      "autoUpdate": true
    }
  },
  "enabledPlugins": { "yolk@avaturn": true }
}
```

Each user still runs `/yolk:setup <code>`.

### Troubleshooting

- Status: `python3 ~/.claude/yolk/bin/launcher.py status`
- Run the background job now: `launchctl kickstart gui/$(id -u)/com.yolk.agent`
- Logs: `~/.claude/yolk/audit/agent.log` and `poller.log`
- Screen Time missing (`"full_disk_access": false` in status): grant Full Disk Access to the path
  that a new Claude Code session shows. It is usually Apple's `Python.app`.
