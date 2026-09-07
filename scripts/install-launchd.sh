#!/bin/bash
# install-launchd.sh — install/uninstall/status for the Task Router launchd agent.
#
#   ./scripts/install-launchd.sh install     sync code to ~/taskrouter/app + write plist
#   ./scripts/install-launchd.sh uninstall   unload (if loaded) + remove plist (keeps data)
#   ./scripts/install-launchd.sh status      show whether launchd has the agent
#
# install never loads the agent — run the printed launchctl command yourself.
set -euo pipefail

LABEL="com.user.taskrouter"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"
TASKROOT="$HOME/taskrouter"
APPDIR="$TASKROOT/app"
PYTHON="/opt/anaconda3/bin/python3"
PORT="${TASKROUTER_PORT:-3459}"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

cmd_install() {
  mkdir -p "$TASKROOT/logs" "$TASKROOT/artifacts" "$APPDIR" "$HOME/Library/LaunchAgents"

  # Sync an installed copy of the package onto the internal disk so the service
  # keeps running even if the external volume unmounts.
  rsync -a --delete "$REPO_DIR/taskrouter/" "$APPDIR/taskrouter/"

  cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>${PYTHON}</string>
        <string>-m</string>
        <string>uvicorn</string>
        <string>taskrouter.main:app</string>
        <string>--host</string>
        <string>127.0.0.1</string>
        <string>--port</string>
        <string>${PORT}</string>
    </array>
    <key>WorkingDirectory</key>
    <string>${TASKROOT}</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PYTHONPATH</key>
        <string>${APPDIR}</string>
        <key>TASKROUTER_HOME</key>
        <string>${TASKROOT}</string>
        <key>TASKROUTER_PORT</key>
        <string>${PORT}</string>
        <key>NO_PROXY</key>
        <string>127.0.0.1,localhost</string>
        <key>no_proxy</key>
        <string>127.0.0.1,localhost</string>
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>${TASKROOT}/logs/taskrouter.out.log</string>
    <key>StandardErrorPath</key>
    <string>${TASKROOT}/logs/taskrouter.err.log</string>
</dict>
</plist>
PLIST

  echo "installed:"
  echo "  plist:  $PLIST"
  echo "  code:   $APPDIR/taskrouter (synced from $REPO_DIR)"
  echo "  data:   $TASKROOT (taskrouter.db, logs/, artifacts/)"
  echo
  echo "agent NOT loaded. load it manually with:"
  echo "  launchctl load \"$PLIST\""
  echo "unload with:"
  echo "  launchctl unload \"$PLIST\""
}

cmd_uninstall() {
  if launchctl list "$LABEL" >/dev/null 2>&1; then
    launchctl unload "$PLIST" 2>/dev/null || true
    echo "unloaded $LABEL"
  fi
  if [ -f "$PLIST" ]; then
    rm -f "$PLIST"
    echo "removed $PLIST"
  fi
  echo "runtime data in $TASKROOT was NOT deleted"
}

cmd_status() {
  if launchctl list "$LABEL" >/dev/null 2>&1; then
    launchctl list "$LABEL"
    echo "status: loaded"
  else
    echo "status: not loaded (plist: $PLIST)"
    [ -f "$PLIST" ] && echo "plist exists; load with: launchctl load \"$PLIST\""
  fi
}

case "${1:-}" in
  install) cmd_install ;;
  uninstall) cmd_uninstall ;;
  status) cmd_status ;;
  *) echo "usage: $0 {install|uninstall|status}" >&2; exit 2 ;;
esac
