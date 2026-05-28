#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PLIST_NAME="com.chicagopipeline.bouncepoller.plist"
TARGET="$HOME/Library/LaunchAgents/$PLIST_NAME"

mkdir -p "$HOME/Library/LaunchAgents"
sed "s|__PROJECT_DIR__|$PROJECT_ROOT|g" \
  "$PROJECT_ROOT/scripts/$PLIST_NAME.template" > "$TARGET"

launchctl unload "$TARGET" 2>/dev/null || true
launchctl load -w "$TARGET"

echo "Installed: $TARGET"
echo "Runs every 3600s. Logs: $PROJECT_ROOT/data/bounce_poller.{log,err.log}"
echo "Uninstall: launchctl unload $TARGET && rm $TARGET"
