---
description: Check the exchange for Manager-AI questions and answer them now
---

Process pending HR-for-AI check-in questions immediately.

1. Run in the foreground with progress:
   ```
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/employee_runner.py" --verbose
   ```
2. Summarize for the user what happened: how many question files were answered, whether the privacy gate redacted anything, and remind them the exact payloads sent are auditable in `~/.hrforai/audit/` and the exchange repo's `outbox/`.
3. If it fails: missing config → suggest `/hrforai-agent:setup`; git/auth errors → `gh auth login` or ask the manager to grant exchange-repo access.
