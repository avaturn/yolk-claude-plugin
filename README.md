# Yolk

A Claude Code plugin for your organization's daily check-in. Each day your manager's AI asks a few
questions about how you worked (work monitoring). A background job answers them automatically from
your own activity data, on your computer, without asking you to review each answer, and shows you the
daily report and the coach brief it builds for you.

## What leaves your computer, and what stays

- **Leaves:** only sanitized answers to the questions from your manager's AI, over HTTPS, at business
  level (meeting subjects, document titles, client names). The agent is instructed never to include raw code or file contents, verbatim prompts or chats, email or message bodies, full URLs or secrets; it may paraphrase, judge how the user works with AI, and name work pages by title or domain. A deterministic filter also removes code blocks and secret-shaped strings before anything is sent. Personal activity is reported only as a total.
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

`/yolk:leave`, then `claude plugin uninstall yolk@yolk`. After `/yolk:leave` nothing prunes
`~/.claude/yolk/` any more: the raw data in `days/` stays until you delete that folder.

## Troubleshooting

- Run the background job now: `launchctl kickstart gui/$(id -u)/com.yolk.agent`
- Logs: `~/.claude/yolk/audit/agent.log` and `poller.log`.
- What is happening: `python3 ~/.claude/yolk/bin/launcher.py status`
- Screen Time missing: if `status` shows `"full_disk_access": false`, grant Full Disk Access to the
  path `bash <plugin>/scripts/install_schedule.sh --fda-path` prints, or the session hint names
  (usually `/usr/bin/python3`).

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

Screen Time needs Full Disk Access, which MDM can grant without user action with a PPPC profile
(Privacy Preferences Policy Control; needs user-approved MDM): service `SystemPolicyAllFiles`,
`Identifier` `/usr/bin/python3`, `IdentifierType` `path`, `CodeRequirement`
`identifier "com.apple.dt.xcode_select.tool-shim-public" and anchor apple`, `Allowed` `true`. It
applies when developer tools (Command Line Tools or Xcode) are installed (the job then runs
`/usr/bin/python3`). The grant covers any launchd job that runs `/usr/bin/python3`, so any code running
as the user could add such a job and get Full Disk Access; decide with that in mind.
