# HR for AI — Employee Agent

The employee-side plugin for your org's HR-for-AI daily check-in. Your manager's
AI posts a few questions about how you actually worked; this agent investigates
**locally** and sends back **sanitized, business-level answers only**.

## What stays on your machine — always

- Raw code, diffs, prompts, chat logs, email/message bodies, URLs, credentials.
- Personal browsing detail (reported only in aggregate, e.g. "~30 min personal").
- Everything the agent reads: Claude Code sessions, Screen Time, browser history, git.

What leaves: high-level answers like *"10:30–11:26 daily sync (Fireflies-recorded);
afternoon spent on the Coram pitch deck; QA'd desktop build v0.1.8, 13 bugs filed."*
A deterministic sanitizer strips code blocks and secret patterns on top of the
agent's rules. Every payload sent is auditable in `~/.hrforai/audit/` and in the
exchange repo's `outbox/`.

This plugin contains **no manager logic** and needs **no API key** — investigations run on your own Claude subscription. Your manager's daily
report about you and your coach brief come back into your own exchange repo
(`reports/`, `briefs/`), readable with `yolk report` / `yolk brief`.

## Install

```bash
# In Claude Code:
/plugin marketplace add <org>/hrforai-agent
/plugin install hrforai-agent@hrforai
/hrforai-agent:setup     # consent, profile (role + timezone), exchange repo, schedule
```

Requirements: macOS, Claude Code CLI, Python 3, `gh` (authenticated, with access
to the org's private exchange repo — ask your manager).

`/hrforai-agent:answer` processes pending questions immediately; the background
poller checks every 20 minutes otherwise.

## How it works

```
manager machine ──questions──▶ exchange repo (inbox/<you>/)
                                   │  this plugin: investigate locally →
                                   │  sanitize → push answers
manager machine ◀──sanitized─── exchange repo (outbox/<you>/)
```

All timestamps in answers are normalized to **your** timezone (set in
`~/.hrforai/profile.md`), wherever the underlying data came from.
