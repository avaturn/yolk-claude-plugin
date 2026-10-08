# Yolk — Employee Agent

The employee-side Claude Code plugin for your org's Yolk daily check-in. Your
manager's AI posts a few questions about how you actually worked; this agent
investigates **locally** and sends back **sanitized, business-level answers only**.

## What stays on your machine — always

- Raw code, diffs, prompts, chat logs, email/message bodies, URLs, credentials.
- Personal browsing detail (reported only in aggregate, e.g. "~30 min personal").
- Everything the agent reads: Claude Code sessions, Screen Time, browser history, git.

What leaves: high-level answers like *"10:30–11:26 daily sync (Fireflies-recorded);
afternoon spent on the Coram pitch deck; QA'd desktop build v0.1.8, 13 bugs filed."*
A deterministic sanitizer strips code blocks and secret patterns on top of the
agent's rules. Every payload sent is kept in `~/.claude/yolk/audit/`.

This plugin contains **no manager logic** and needs **no API key** — investigations
run on your own Claude subscription. Your manager's daily report about you and your
coach brief come back to `~/.claude/yolk/` — read them with `/yolk:report` and `/yolk:brief`.

## Install

Requirements: macOS, Python 3, and the Claude Code CLI with `claude` on PATH (the
background poller runs it). Check with `claude --version`.

### From GitHub (employees)

1. Ask your manager for read access to the plugin repo (today `gregory156/yolk-plugin-agent`).
2. In Claude Code, add the marketplace and install the plugin:
   ```
   /plugin marketplace add gregory156/yolk-plugin-agent
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

- The invite code carries no server address. The plugin uses `YOLK_SERVER`, default `http://localhost:8080` (until the
  production server is deployed).
- Plain HTTP is accepted only for `localhost`, `127.0.0.1` and `::1`.

### Uninstall

1. `/yolk:leave` revokes this Mac's token and stops the poller.
2. `claude plugin uninstall yolk@yolk`, then `claude plugin marketplace remove yolk`.
3. Delete `~/.claude/yolk/` if you do not need your briefs, reports and audit trail.

To test the plugin against a local server by hand, follow [docs/manual-check.md](docs/manual-check.md).

| Command | What it does |
|---|---|
| `/yolk:setup <code>` | Consent, link this Mac with the invite, install the background poller |
| `/yolk:answer` | Answer pending questions now (otherwise the poller checks every 20 min) |
| `/yolk:brief [date]` | Your coach brief |
| `/yolk:report [date]` | The report your manager received about you |
| `/yolk:leave` | Revoke this Mac's token and stop the poller |

## How it works

```
Yolk server ──question sets──▶ this Mac: investigate locally (claude -p)
                                  → sanitize → POST answers
Yolk server ◀──sanitized answers──┘
Yolk server ──report + brief──▶ ~/.claude/yolk/reports, briefs
```

Device API v1 over HTTPS, one device token per person (stored in
`~/.claude/yolk/agent.json`, mode 600). All timestamps in answers are normalized
to **your** timezone (from your registration), wherever the data came from.
