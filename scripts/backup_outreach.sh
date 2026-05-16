#!/usr/bin/env bash
# Dumps just the outreach/contacts/waves tables from the working DB into
# a timestamped backup file under data/. Run manually before any risky
# operation, or wire to launchd for daily backups.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SRC="${1:-$PROJECT_DIR/data/full.alt.db}"
TIMESTAMP="$(date +%Y-%m-%d-%H%M%S)"
DEST="$PROJECT_DIR/data/outreach_backup_${TIMESTAMP}.sql"

if [ ! -f "$SRC" ]; then
    echo "ERROR: source DB not found: $SRC" >&2
    exit 1
fi

# Dump only the outreach-related tables. SQL text file is human-readable
# and small (~few KB at 10-20/wk volume). sqlite3 CLI only accepts one
# SQL arg, so we feed commands via stdin. To restore: sqlite3 <new.db>
# < $DEST after init_db creates the schema.
sqlite3 "$SRC" <<'EOF' > "$DEST"
.dump outreach
.dump contacts
.dump waves
EOF

# Keep only the last 30 daily backups — older than that gets pruned.
find "$PROJECT_DIR/data" -name 'outreach_backup_*.sql' -type f \
    | sort -r | tail -n +31 | xargs -I {} rm -f {}

echo "Backup written: $DEST"
echo "Restore with: sqlite3 <new.db> < $DEST  (after init_db creates the schema)"
