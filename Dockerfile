# Production image for Render / any Docker-friendly host.
#
# Notes:
#   - python:3.14 (Debian Bookworm-based) — matches local dev's interpreter
#   - shapely / pyproj install via prebuilt wheels (no apt deps required for
#     the webapp's runtime path; pipeline.spatial isn't loaded at request time)
#   - `scripts/init_db.sh` runs at container start to fetch the DB onto the
#     persistent disk if it isn't there already
#   - gunicorn binds to ${PORT} which Render injects (default 8000 for local)
#   - Production webapp reads config from env vars, not argparse — see
#     webapp/wsgi.py
FROM python:3.14

WORKDIR /app

# curl is needed for the DB-download init script.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps first so docker layer caches across code changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Persistent disk mount point. The host (Render) maps /data to a 1 GB volume
# so the SQLite DB survives redeploys.
ENV DB_PATH=/data/full.alt.db
# Canonical scoring YAML — must match the YAML used to score the DB.
# render.yaml overrides this at runtime; keeping it consistent here protects
# against the env var failing to attach.
ENV SCORING_YAML_PATH=config/scoring.yaml
ENV PORT=8000

EXPOSE 8000

# At boot: download DB if missing, then hand off to gunicorn. Two workers
# is plenty for a single-friend audience; 120s timeout accommodates the
# initial DB download window (curl runs before gunicorn starts).
CMD ["bash", "-c", "scripts/init_db.sh && exec gunicorn webapp.wsgi:app --bind 0.0.0.0:${PORT} --workers 2 --timeout 120 --access-logfile -"]
