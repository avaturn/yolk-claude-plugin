# Yolk — Employee Agent

The employee-side Claude Code plugin for your org's Yolk daily check-in. Your
manager's AI posts a few questions about how you actually worked; this agent
investigates **locally** and sends back **sanitized, business-level answers only**.
Raw activity data and the daily summaries it builds for itself are **never sent to Yolk or your manager**; they are processed by Claude under your own account.

## What is never sent — always

- Raw code, diffs, prompts, shell commands, chat logs, email/message bodies, URLs, credentials.
- The raw collector output (prompt previews, commands, every page title and domain): written
  to `~/.claude/yolk/days/<date>.json`, readable by your user only, never sent to Yolk, deleted after 14 days. (Claude reads it under your own account to write the summary.)
- **The daily summaries**: for each finished day of the last 7 the agent writes a private
  summary (what / where / how) to `~/.claude/yolk/days/<date>.summary.json` and answers from it. Never sent to Yolk or your manager.
- Everything the agent reads: Claude Code sessions, Screen Time, git, and the browser history of every Chromium-family browser it finds (Chrome, Arc, Brave, Edge, Chromium and Vivaldi).

What leaves, over HTTPS: only **sanitized answers to the manager AI's questions**, at business level, like
*"10:30–11:26 daily sync (Fireflies-recorded); afternoon spent on the Coram pitch deck."* Personal
activity is reported only in aggregate. A deterministic sanitizer strips code blocks and secret
patterns on top of the agent's rules. Every answer sent is kept in `~/.claude/yolk/audit/`.

This plugin contains **no manager logic** and needs **no API key** — summaries and
investigations run on your own Claude subscription (Claude Opus 5.5, high effort). Your manager sees your sanitized answers and a team digest you never see. You see your own daily activity
report (the local summary, `/yolk:report`) and your coach brief (`/yolk:brief`, from your answers).

## Install

Requirements: macOS, Python 3, and the Claude Code CLI with `claude` on PATH (the
background poller runs it). Check with `claude --version`.

### From GitHub (employees)

1. Ask your manager for read access to the plugin repo `avaturn/yolk-plugin-agent`.
2. In Claude Code, add the marketplace and install the plugin:
   ```
   /plugin marketplace add avaturn/yolk-plugin-agent
   /plugin install yolk@yolk
   ```
3. Open a new Claude Code session, so the `/yolk:*` commands load.
4. Run `/yolk:setup <invite code>` with the code your manager sent (like `K7QP-3FXA`;
   case and dashes do not matter). Setup asks for your consent, links this Mac, and installs
   the background poller.
5. Turn on auto-update once: `/plugin` → Marketplaces → `yolk` → Enable auto-update. Without it you
   update by hand (see Updates). Skip this if your organization manages it (the toggle is then hidden).

### From a local checkout (development)

1. Add the checkout as a marketplace and install:
   ```bash
   claude plugin marketplace add /path/to/yolk-plugin-agent
   claude plugin install yolk@yolk
   ```
2. Open a new Claude Code session.
3. After you edit the checkout, reinstall. Claude Code runs a cached copy
   (`~/.claude/plugins/cache/yolk/yolk/<version>/`), and `claude plugin update` does
   nothing while the version in `.claude-plugin/plugin.json` is unchanged:
   ```bash
   claude plugin uninstall yolk@yolk && claude plugin install yolk@yolk
   ```
   Then open a new session.

### Server address

- The invite code carries no server address. The plugin uses `https://plugin.dev.yolk.coach`.
- To use another server (a local one), start Claude Code with `YOLK_SERVER=<base URL>` set.
- Plain HTTP is accepted only for `localhost`, `127.0.0.1` and `::1`.

### Uninstall

1. `/yolk:leave` revokes this Mac's token and stops the poller.
2. `claude plugin uninstall yolk@yolk`, then `claude plugin marketplace remove yolk`.
3. Delete `~/.claude/yolk/` if you do not need your briefs, old reports, local summaries and audit trail.

| Command | What it does |
|---|---|
| `/yolk:setup <code>` | Consent, link this Mac with the invite, install the background poller (starts at once, at every login, and every 20 min) |
| `/yolk:brief [date]` | Your coach brief; if none has arrived yet it says what the poller is doing and tells you when it is ready |
| `/yolk:report [date]` | Your own daily activity report (the local summary of a day) |
| `/yolk:leave` | Revoke this Mac's token and stop the poller |

## How it works

```
Yolk server ──question sets──▶ this Mac: build that day's local summary if missing
                                  (4 collectors → days/<date>.json → claude -p, no tools → days/<date>.summary.json)
                                  → investigate locally (claude -p), starting from the summary
                                  → sanitize → POST answers
Yolk server ◀──sanitized answers──┘
Yolk server ──coach brief──▶ ~/.claude/yolk/briefs
```

The poller runs at load, at every login and every 20 minutes; each run sends unsent answers, fetches briefs, then for each question set builds that
day's summary if missing and answers it. Only after the answers are out does it fill in the local summary
of other finished days (up to 2 per poll) from 7 days ago to yesterday. A day whose summary fails to build 3 times is
given up on (`audit/summary-<date>.failed.json`, local). A day is summarized only after it has ended in
both your timezone and the machine's. `python3 scripts/employee_runner.py summarize [--date YYYY-MM-DD | --limit N]`
builds summaries on demand.

Device API v1 over HTTPS, one device token per person (stored in
`~/.claude/yolk/agent.json`, mode 600). All timestamps in answers are
normalized to **your** timezone (from your registration), wherever the data came from.

**Full Disk Access.** Screen Time is readable only by programs with Full Disk Access. The
background poller runs as the `python3` binary named in its launchd plist (not your
terminal), so grant it to that binary — `/yolk:setup` and `install_schedule.sh` print its
path. Without it the summary simply notes that Screen Time was unreadable.

**Updates.** launchd does not run the plugin copy directly: the poller's plist runs
`~/.claude/yolk/bin/launcher.py`, a copy of `scripts/launcher.py` that never moves and hands over to the
newest installed plugin version (Claude Code keeps each version in its own directory and deletes an
old one 14 days after an update). So an update reaches the poller by itself on its next run. A `SessionStart` hook (`hooks/hooks.json`) runs `employee_runner.py heal` at the start of every Claude Code session, so an old install
migrates with no command: it is silent unless it changed something, uses no network, and never fails a session. When
`/yolk:setup` or `/yolk:brief` runs, the plugin also re-installs the plist if the launcher copy is
outdated or the plist is an older version-pinned one, and installs a missing plist (only for a linked,
installed plugin on the default `~/.claude/yolk`; never from a dev checkout or test home; `/yolk:leave`
removes it for good). With auto-update on, nothing else is needed. By hand: `claude plugin update yolk@yolk`
updates the plugin (`claude plugin marketplace update yolk` only refreshes the catalog); a new session
loads it, or run `/reload-plugins`. The two prompts the agent gives
Claude live in `charters/`, not `agents/`, so they do not show up as subagents in your sessions.

**What is happening now.** The poller writes `~/.claude/yolk/status.json` (phase, date, check-in dates; never content).
`employee_runner.py status` prints it with live facts (poll running, activity reports ready, briefs, open
check-ins); `status --wait N` waits up to N seconds for a running poll to finish, then for an open check-in's
brief. `/yolk:brief`, `/yolk:report` and `/yolk:setup` run it in the background and the session tells you when
the result is ready. `status` never heals the poller.

## For admins

To make every machine register the `yolk` marketplace, install the plugin and keep it up to date, set
this in managed settings (field names and nesting copied from
[Manage Claude Code plugins for your organization](https://code.claude.com/docs/en/plugins/org),
sections "Pre-install and require plugins" and "Set update policy"; the key is the marketplace's own `name`,
here `yolk`):

```json
{
  "extraKnownMarketplaces": {
    "yolk": {
      "source": { "source": "github", "repo": "avaturn/yolk-plugin-agent" },
      "autoUpdate": true
    }
  },
  "enabledPlugins": {
    "yolk@yolk": true
  }
}
```

With `autoUpdate` set in managed settings the user's `/plugin` toggle is refused. `enabledPlugins` is optional:
it installs and force-enables the plugin at the next session start (users still run `/yolk:setup <code>`).
The repo is private, so each machine needs git access to it.
