---
description: Unlink this Mac from Yolk and stop the background agent
---

1. Confirm the user wants to unlink this Mac.
2. `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/employee_runner.py" leave`
3. `launchctl unload ~/Library/LaunchAgents/com.yolk.agent.plist 2>/dev/null; rm -f ~/Library/LaunchAgents/com.yolk.agent.plist; rm -rf ~/.claude/yolk/bin`
4. Tell them `~/.claude/yolk/` still holds their briefs, reports and audit trail (raw data in `days/` is deleted after 14 days), and they may delete it.
