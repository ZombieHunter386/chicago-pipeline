"""WSGI entry point for production servers (gunicorn).

Configuration is via env vars so the same image runs in any host:
  DB_PATH               (default: data/full.alt.db)
  SCORING_YAML_PATH     (default: config/scoring.yaml — i.e. canonical)
  FEATURE_OUTREACH      (set to 'true' / '1' to enable, default off)
  WEBAPP_USER           (basic-auth username; absence disables auth)
  WEBAPP_PASSWORD       (basic-auth password; absence disables auth)
  ESRI_API_KEY          (Esri location-services API key; satellite basemap
                         falls back to anonymous Esri without it — which
                         hits "Account Limit Exceeded" under deployed load)

Local dev still uses `python -m webapp --db ...` (the argparse CLI in
__main__.py). gunicorn reaches `webapp.wsgi:app` and skips that CLI."""
from __future__ import annotations
import os
from pathlib import Path

from pipeline.db import init_db
from webapp.app import create_app


_db_path = Path(os.environ.get("DB_PATH", "data/full.alt.db"))

# Run pending schema migrations (_LATER_COLUMNS) against the deployed DB
# before serving any request. The DB on the persistent volume is downloaded
# once from DB_DOWNLOAD_URL (see scripts/init_db.sh) and never re-created, so
# when a deploy adds a column the old DB lacks it — and the first query that
# SELECTs the new column (e.g. /api/parcels selects score_adu/score_redev)
# raises "no such column", which the global error handler turns into a 500
# and the UI renders as "no properties". init_db's ALTER TABLE ADD COLUMN is
# idempotent and metadata-only (instant, non-destructive), so it is safe to
# run on every boot. Mirrors the migration __main__.py runs for local dev.
init_db(_db_path)
_scoring_yaml_env = os.environ.get("SCORING_YAML_PATH")
_scoring_yaml_path = Path(_scoring_yaml_env) if _scoring_yaml_env else None
_feature_outreach = os.environ.get("FEATURE_OUTREACH", "").lower() in {"true", "1"}

# Defense in depth: outreach is local-only by design. If we detect we're
# running in a deployed context (basic-auth env vars present, which only get
# set on Railway), refuse to enable outreach even if the flag is set.
_is_deployed = bool(os.environ.get("WEBAPP_USER") and os.environ.get("WEBAPP_PASSWORD"))
if _is_deployed and _feature_outreach:
    import sys
    print(
        "WARNING: FEATURE_OUTREACH=true was set in a deployed context "
        "(WEBAPP_USER/WEBAPP_PASSWORD present). Outreach is local-only by "
        "design — disabling the flag.",
        file=sys.stderr,
    )
    _feature_outreach = False

app = create_app(
    db_path=_db_path,
    scoring_yaml_path=_scoring_yaml_path,
    feature_outreach=_feature_outreach,
    esri_api_key=os.environ.get("ESRI_API_KEY"),
)
