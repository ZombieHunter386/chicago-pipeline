#!/usr/bin/env bash
# Install the Due Today digest as a launchd job that fires daily at 9am
# local time. Reads GMAIL_SENDER_ADDRESS from .env or arg 1.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TEMPLATE="$PROJECT_DIR/scripts/com.chicagopipeline.duedigest.plist.template"
PLIST_DEST="$HOME/Library/LaunchAgents/com.chicagopipeline.duedigest.plist"

SENDER="${1:-}"
if [ -z "$SENDER" ] && [ -f "$PROJECT_DIR/.env" ]; then
    SENDER=$(grep '^GMAIL_SENDER_ADDRESS=' "$PROJECT_DIR/.env" | cut -d= -f2-)
fi
if [ -z "$SENDER" ]; then
    echo "ERROR: GMAIL_SENDER_ADDRESS not provided. Pass as arg 1 or set in .env." >&2
    exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents"
sed -e "s|__PROJECT_DIR__|$PROJECT_DIR|g" \
    -e "s|__SENDER__|$SENDER|g" \
    "$TEMPLATE" > "$PLIST_DEST"

# Unload first in case it's already loaded
launchctl unload "$PLIST_DEST" 2>/dev/null || true
launchctl load -w "$PLIST_DEST"

echo "Installed: $PLIST_DEST"
echo "Daily digest will fire at 9:00 local time."
echo ""
echo "Verify with: launchctl list | grep chicagopipeline"
echo "Test now with: $PROJECT_DIR/.venv/bin/python -m pipeline.due_digest --dry-run"
echo "Uninstall with: launchctl unload $PLIST_DEST && rm $PLIST_DEST"
