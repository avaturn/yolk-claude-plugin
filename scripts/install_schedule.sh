#!/bin/bash
# Install the Yolk Employee Agent background poller (launchd, at load/login and every poll_interval_s
# from ~/.claude/yolk/agent.json, default 20 min).
set -euo pipefail

PLUGIN_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
YOLK_HOME="${YOLK_HOME:-$HOME/.claude/yolk}"
PLIST="$HOME/Library/LaunchAgents/com.yolk.agent.plist"
OLD_PLIST="$HOME/Library/LaunchAgents/com.hrforai.agent.plist"  # v1.1 git poller
LOG_DIR="$YOLK_HOME/audit"
mkdir -p "$LOG_DIR" "$(dirname "$PLIST")"

CLAUDE_BIN="$(command -v claude || true)"
PY_BIN="$(command -v python3)"
INTERVAL="$("$PY_BIN" -c 'import json,sys; print(int(json.load(open(sys.argv[1])).get("poll_interval_s", 1200)))' \
  "$YOLK_HOME/agent.json" 2>/dev/null || echo 1200)"

if [ -f "$OLD_PLIST" ]; then
  launchctl unload "$OLD_PLIST" 2>/dev/null || true
  rm -f "$OLD_PLIST"
  echo "Removed the old git-based poller (com.hrforai.agent)."
fi

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.yolk.agent</string>
  <key>ProgramArguments</key>
  <array>
    <string>${PY_BIN}</string>
    <string>${PLUGIN_ROOT}/scripts/employee_runner.py</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>$(dirname "${CLAUDE_BIN:-/usr/local/bin/x}"):/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    <key>YOLK_HOME</key><string>${YOLK_HOME}</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>StartInterval</key><integer>${INTERVAL}</integer>
  <key>StandardOutPath</key><string>${LOG_DIR}/poller.log</string>
  <key>StandardErrorPath</key><string>${LOG_DIR}/poller.err</string>
</dict>
</plist>
EOF

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
echo "Installed: com.yolk.agent (polls every $((INTERVAL / 60)) min). Logs: $LOG_DIR"
# launchd runs python3 itself, not your terminal: Screen Time needs Full Disk Access for THIS binary
echo "Full Disk Access (System Settings > Privacy & Security): add $("$PY_BIN" -c 'import os,sys; print(os.path.realpath(sys.executable))')"
[ -n "$CLAUDE_BIN" ] || echo "WARNING: 'claude' CLI not found on PATH — investigations will fail until it is installed."
