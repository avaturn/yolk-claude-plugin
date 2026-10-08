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
| `/yolk:brief [date]` | Your coach brief |
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

**Updates.** The poller's launchd plist points at the installed plugin copy. After an update, the
next time the runner runs (`/yolk:setup` or `/yolk:brief`, and the poller itself; `/yolk:report` and `/yolk:leave` do not run it) the plugin re-points the plist at the new copy (only for the real installed plugin and
your real `~/.claude/yolk`; never from a dev checkout or test home, and a missing plist is installed only for a linked, installed plugin using the default `~/.claude/yolk`; `/yolk:leave` removes it for good). The two prompts the agent gives
Claude live in `charters/`, not `agents/`, so they do not show up as subagents in your sessions.
