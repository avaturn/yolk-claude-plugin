#!/bin/bash
# Install the HR-for-AI Employee Agent background poller (launchd, every 20 min).
set -euo pipefail

PLUGIN_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PLIST="$HOME/Library/LaunchAgents/com.hrforai.agent.plist"
LOG_DIR="$HOME/.hrforai/audit"
mkdir -p "$LOG_DIR"

CLAUDE_BIN="$(command -v claude || true)"
GH_BIN="$(command -v gh || true)"
PY_BIN="$(command -v python3)"
EXTRA_PATH="$(dirname "${CLAUDE_BIN:-/usr/local/bin/x}"):$(dirname "${GH_BIN:-/usr/local/bin/x}")"

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.hrforai.agent</string>
  <key>ProgramArguments</key>
  <array>
    <string>${PY_BIN}</string>
    <string>${PLUGIN_ROOT}/scripts/employee_runner.py</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>${EXTRA_PATH}:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
  </dict>
  <key>StartInterval</key><integer>1200</integer>
  <key>StandardOutPath</key><string>${LOG_DIR}/poller.log</string>
  <key>StandardErrorPath</key><string>${LOG_DIR}/poller.err</string>
</dict>
</plist>
EOF

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
echo "Installed: com.hrforai.agent (polls every 20 min). Logs: $LOG_DIR"
