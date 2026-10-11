#!/bin/bash
# Install the Yolk Employee Agent background poller (launchd: at load/login, after wake, and every
# poll_interval_s from ~/.claude/yolk/agent.json, default 20 min).
set -euo pipefail

PLUGIN_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
YOLK_HOME="${YOLK_HOME:-$HOME/.claude/yolk}"
PLIST="$HOME/Library/LaunchAgents/com.yolk.agent.plist"
OLD_PLIST="$HOME/Library/LaunchAgents/com.hrforai.agent.plist"  # v1.1 git poller
LOG_DIR="$YOLK_HOME/audit"
mkdir -p "$LOG_DIR" "$(dirname "$PLIST")" "$YOLK_HOME/bin"
# launchd runs a launcher at a path that never changes: Claude Code deletes old plugin version dirs
LAUNCHER="$YOLK_HOME/bin/launcher.py"
cp "$PLUGIN_ROOT/scripts/launcher.py" "$LAUNCHER"

CLAUDE_BIN="$(command -v claude || true)"
PY_BIN="$(command -v python3)"
INTERVAL="$("$PY_BIN" -c 'import json,sys; print(int(json.load(open(sys.argv[1])).get("poll_interval_s", 1200)))' \
  "$YOLK_HOME/agent.json" 2>/dev/null || echo 1200)"

# StartCalendarInterval, not StartInterval: launchd misses StartInterval firings while the Mac sleeps
# (man launchd.plist), so the first poll after a wake could come up to one interval late; a calendar job
# missed during sleep runs once, when the Mac wakes. Minutes: every INTERVAL/60 within the hour (0/20/40
# by default). An interval of an hour or more, or one that does not divide 3600 evenly, falls back to
# hourly at minute 0.
MINUTES="$("$PY_BIN" -c 'import sys; i = int(sys.argv[1]); print(" ".join(str(m) for m in range(0, 60, i // 60)) if 60 <= i < 3600 and 3600 % i == 0 else "0")' "$INTERVAL")"
SCHEDULE=""
for m in $MINUTES; do SCHEDULE="$SCHEDULE<dict><key>Minute</key><integer>$m</integer></dict>"; done

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
    <string>${LAUNCHER}</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>$(dirname "${CLAUDE_BIN:-/usr/local/bin/x}"):/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    <key>YOLK_HOME</key><string>${YOLK_HOME}</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>StartCalendarInterval</key><array>${SCHEDULE}</array>
  <key>StandardOutPath</key><string>${LOG_DIR}/poller.log</string>
  <key>StandardErrorPath</key><string>${LOG_DIR}/poller.err</string>
</dict>
</plist>
EOF

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
echo "Installed: com.yolk.agent (polls at login, after wake, and at minute(s) ${MINUTES// /,} of every hour). Logs: $LOG_DIR"
# launchd runs python3 itself, not your terminal: Screen Time needs Full Disk Access for THIS binary
echo "Full Disk Access (System Settings > Privacy & Security): add $("$PY_BIN" -c 'import os,sys; print(os.path.realpath(sys.executable))')"
[ -n "$CLAUDE_BIN" ] || echo "WARNING: 'claude' CLI not found on PATH — investigations will fail until it is installed."
