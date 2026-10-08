# Yolk — Employee Agent

The employee-side Claude Code plugin for your org's Yolk daily check-in. This agent
reads your local work evidence, builds a **sanitized, business-level summary of each
past workday** (what you did, where, how) and sends it to your manager's AI, so your
report starts right away. If the summary leaves gaps, the manager's AI posts a few
questions and this agent investigates **locally** and sends back **sanitized answers only**.

## What stays on your machine — always

- Raw code, diffs, prompts, shell commands, chat logs, email/message bodies, URLs, credentials.
- The raw collector output (prompt previews, commands, every page title and domain): it is
  written to `~/.claude/yolk/days/<date>.json`, readable by your user only, never sent, and deleted after 14 days.
- Personal activity detail: it leaves only as a total ("35 personal minutes"), never what it was.
- Everything the agent reads: Claude Code sessions, Screen Time, git, and the browser history of every Chromium-family browser it finds (Chrome, Arc, Brave, Edge, Chromium and Vivaldi).

What leaves, over HTTPS:

- **A daily summary** of each past workday: an overview, active and personal minutes, time
  blocks with *what / where / how* (like *"10:30–11:26 daily sync (Fireflies-recorded)"*,
  *"afternoon on the Coram pitch deck"*), the work items of the day, and which sources could be read.
  A new day's summary is built and sent on the first poll after the day ends — no waiting for questions.
- **Answers** to the manager's follow-up questions about gaps.

A deterministic sanitizer strips code blocks and secret patterns on top of the agent's
rules. Every payload sent is kept in `~/.claude/yolk/audit/` (`summary-<date>.sent.json`).

This plugin contains **no manager logic** and needs **no API key** — summaries and
investigations run on your own Claude subscription. Your manager's daily report about you and your
coach brief come back to `~/.claude/yolk/` — read them with `/yolk:report` and `/yolk:brief`.

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
   case and dashes do not matter). Setup asks for your consent, links this Mac, and offers
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
3. Delete `~/.claude/yolk/` if you do not need your briefs, reports and audit trail.

| Command | What it does |
|---|---|
| `/yolk:setup <code>` | Consent, link this Mac with the invite, install the background poller |
| `/yolk:answer` | Send missing daily summaries and answer pending questions now (otherwise the poller runs every 20 min) |
| `/yolk:brief [date]` | Your coach brief |
| `/yolk:report [date]` | The report your manager received about you |
| `/yolk:leave` | Revoke this Mac's token and stop the poller |

## How it works

```
this Mac: run the 4 collectors → days/<date>.json (raw, stays here)
          → one local claude -p run (no tools) → sanitize → POST /v1/summaries/<date>
Yolk server ◀──sanitized daily summary──┘      (the server starts your report now)
Yolk server ──question sets (gaps only)──▶ this Mac: investigate locally (claude -p)
                                              starting from that summary
                                              → sanitize → POST answers
Yolk server ──report + brief──▶ ~/.claude/yolk/reports, briefs
```

Every 20 minutes the poller sends what is still unsent, then builds a summary for each
past day that has none (from the day before you linked, at most the last 7 days), then
answers questions. A day the server refuses (HTTP 422) is not retried, and a day whose summary fails to build 3 times is given up on (`audit/summary-<date>.failed.json`); the manager's AI then asks questions instead. A day is summarized only after it has ended in both your timezone and the machine's. `summarize`
(`python3 scripts/employee_runner.py summarize [--date YYYY-MM-DD]`) does the summary step on demand.

Device API v1 over HTTPS, one device token per person (stored in
`~/.claude/yolk/agent.json`, mode 600). All timestamps in summaries and answers are
normalized to **your** timezone (from your registration), wherever the data came from.

**Full Disk Access.** Screen Time is readable only by programs with Full Disk Access. The
background poller runs as the `python3` binary named in its launchd plist (not your
terminal), so grant it to that binary — `/yolk:setup` and `install_schedule.sh` print its
path. Without it the summary simply marks Screen Time as unreadable.

**Updates.** The poller's launchd plist points at the installed plugin copy. After an update, the
next time any `/yolk` command runs the plugin re-points the plist at the new copy (only if you
installed the poller; it is never installed behind your back). The two prompts the agent gives
Claude live in `charters/`, not `agents/`, so they do not show up as subagents in your sessions.
