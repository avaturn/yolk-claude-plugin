# Yolk

A Claude Code plugin for your organization's daily check-in. Once a day the Yolk server asks you
one question about your workday. This plugin answers it from your own activity data, on your
computer, and shows you the daily report and the coach brief it builds for you.

## What leaves your computer, and what stays

- **Leaves:** only the sanitized answer to the server's daily question, over HTTPS, at business
  level (meeting subjects, document titles, client names). Code, prompts, commands, messages,
  URLs and secrets are filtered out, and personal activity is reported only as a total.
- **Stays:** your raw activity data (Claude Code sessions, Screen Time, git, browser history of
  Chrome, Arc, Brave, Edge, Chromium and Vivaldi) and your daily activity reports. Claude
  processes them under your own account. They are never sent to Yolk or your manager.
- Everything sent is kept in `~/.claude/yolk/audit/`. No API key is needed.

## Install

1. In Claude Code: `/plugin marketplace add avaturn/yolk-plugin-agent`, then
   `/plugin install yolk@yolk`. Open a new session.
2. Run `/yolk:setup <invite code>` with the code your manager sent. It asks for your consent, links
   the computer and starts a background job (at login, right after the Mac wakes, and every 20 minutes).
3. Turn on auto-update once: `/plugin` → Marketplaces → `yolk` → Enable auto-update
   (skip it if your organization manages updates).

## Commands

| Command | What it does |
|---|---|
| `/yolk:setup <code>` | Consent, link this computer, start the background job |
| `/yolk:brief [date]` | Your coach brief; if it is not ready, says what is happening |
| `/yolk:report [date]` | Your own daily activity report |
| `/yolk:leave` | Unlink this computer and stop the background job |

## Uninstall

`/yolk:leave`, then `claude plugin uninstall yolk@yolk`. Delete `~/.claude/yolk/` if you do not
need your briefs and reports.

## Troubleshooting

- Run the background job now: `launchctl kickstart -k gui/$(id -u)/com.yolk.agent`
- Logs: `~/.claude/yolk/audit/agent.log` and `poller.log`.
- What is happening: `python3 ~/.claude/yolk/bin/launcher.py status`

## For admins

Register the marketplace on every machine, keep it up to date and optionally install the plugin
(managed settings; see [the docs](https://code.claude.com/docs/en/plugins/org)):

```json
{
  "extraKnownMarketplaces": {
    "yolk": {
      "source": { "source": "github", "repo": "avaturn/yolk-plugin-agent" },
      "autoUpdate": true
    }
  },
  "enabledPlugins": { "yolk@yolk": true }
}
```

The repo is private: each machine needs git access to it. Users still run `/yolk:setup <code>`.
