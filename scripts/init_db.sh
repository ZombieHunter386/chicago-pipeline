#!/usr/bin/env bash
# Download the SQLite DB on first container boot if it isn't already on the
# persistent disk. Designed for hosts that mount /data as a volume — the
# 619 MB DB downloads once at provisioning, then survives every redeploy.
#
# Env vars:
#   DB_PATH           target path on the persistent disk (default: /data/full.alt.db)
#   DB_DOWNLOAD_URL   public URL to fetch the DB from (Cloudflare R2 / any HTTPS host)
#
# Behavior:
#   - DB exists already → log size, skip
#   - DB missing + URL set → curl with retries, log result
#   - DB missing + URL unset → fail loud (don't start the app with a missing DB)

set -euo pipefail

DB_PATH="${DB_PATH:-/data/full.alt.db}"
DB_DOWNLOAD_URL="${DB_DOWNLOAD_URL:-}"

filesize() {
    # macOS uses -f%z; GNU stat uses -c%s. We're on Debian in the container,
    # but keep both for local-dev portability.
    stat -c%s "$1" 2>/dev/null || stat -f%z "$1"
}

if [ -f "$DB_PATH" ]; then
    echo "[init_db] DB already at ${DB_PATH} ($(filesize "$DB_PATH") bytes) — skipping download"
    exit 0
fi

if [ -z "$DB_DOWNLOAD_URL" ]; then
    echo "[init_db] ERROR: ${DB_PATH} missing and DB_DOWNLOAD_URL is not set"
    echo "[init_db] Set DB_DOWNLOAD_URL to a public URL of the SQLite DB and redeploy."
    exit 1
fi

mkdir -p "$(dirname "$DB_PATH")"
echo "[init_db] Downloading DB from ${DB_DOWNLOAD_URL} to ${DB_PATH} ..."
# --fail: non-2xx returns become curl errors (so set -e fires)
# --location: follow redirects (R2 may hand out 302s if the URL changes)
# --retry: handle transient network blips during the multi-hundred-MB download
curl --fail --location --retry 5 --retry-delay 5 --retry-connrefused \
     --output "$DB_PATH" \
     "$DB_DOWNLOAD_URL"

echo "[init_db] Downloaded $(filesize "$DB_PATH") bytes to ${DB_PATH}"
