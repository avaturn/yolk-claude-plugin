#!/bin/bash
# Install the Yolk Employee Agent background poller (launchd: at load/login, after wake, and every 2 minutes).
set -euo pipefail

PLUGIN_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
YOLK_HOME="${YOLK_HOME:-$HOME/.claude/yolk}"
PLIST="$HOME/Library/LaunchAgents/com.yolk.agent.plist"
LOG_DIR="$YOLK_HOME/audit"
# macOS checks Full Disk Access for the program launchd starts, symlinks resolved: not your terminal, not
# sys.executable. Launched directly, Apple's Python.app executable is checked by its bundle ID
# (com.apple.python3), so its path and updates do not matter. Not /usr/bin/python3: a shim hard-linked with
# git, clang and others, which macOS may report as /usr/bin/git instead
DEV="$(xcode-select -p 2>/dev/null || true)"
APP="$DEV/Library/Frameworks/Python3.framework/Versions/Current/Resources/Python.app"
if [ -n "$DEV" ] && [ -x "$APP/Contents/MacOS/Python" ]; then PY_BIN="$APP/Contents/MacOS/Python"; FDA_PATH="$APP"
else
  PY_BIN="$(command -v python3)"
  FDA_PATH="$("$PY_BIN" -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$PY_BIN")"
fi
# --fda-path: /yolk:setup grants Full Disk Access before the first poll, so it needs the path without installing
if [ "${1:-}" = "--fda-path" ]; then echo "$FDA_PATH"; exit 0; fi
mkdir -p "$LOG_DIR" "$(dirname "$PLIST")" "$YOLK_HOME/bin"
# launchd runs a launcher at a path that never changes: Claude Code deletes old plugin version dirs
LAUNCHER="$YOLK_HOME/bin/launcher.py"
cp "$PLUGIN_ROOT/scripts/launcher.py" "$LAUNCHER"

CLAUDE_BIN="$(command -v claude || true)"
INTERVAL=120  # seconds; must match POLL_INTERVAL_S in yolk/config.py

# StartCalendarInterval, not StartInterval: launchd misses StartInterval firings while the Mac sleeps
# (man launchd.plist), so the first poll after a wake could come up to one interval late; a calendar job
# missed during sleep runs once, when the Mac wakes. Minutes: every INTERVAL/60 within the hour (0, 2, ...,
# 58 at 120). An interval of an hour or more, or one that does not divide 3600 evenly, falls back to
# hourly at minute 0.
MINUTES="$("$PY_BIN" -c 'import sys; i = int(sys.argv[1]); print(" ".join(str(m) for m in range(0, 60, i // 60)) if 60 <= i < 3600 and 3600 % i == 0 else "0")' "$INTERVAL")"
SCHEDULE=""
for m in $MINUTES; do SCHEDULE="$SCHEDULE<dict><key>Minute</key><integer>$m</integer></dict>"; done

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
echo "Full Disk Access (System Settings > Privacy & Security): add $FDA_PATH"
[ -n "$CLAUDE_BIN" ] || echo "WARNING: 'claude' CLI not found on PATH — investigations will fail until it is installed."
