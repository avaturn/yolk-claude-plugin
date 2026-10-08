---
description: Unlink this Mac from Yolk and stop the background agent
---

1. Confirm with the user that they want to unlink this Mac.
2. Revoke the device token: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/employee_runner.py" leave`.
3. Stop the background agent: `launchctl unload ~/Library/LaunchAgents/com.yolk.agent.plist 2>/dev/null; rm -f ~/Library/LaunchAgents/com.yolk.agent.plist`.
4. Tell them `~/.claude/yolk/` still holds their briefs, reports, audit trail and the raw collector data in `days/` (kept 14 days, then deleted by the agent), and they can delete it all.
