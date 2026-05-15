"""WSGI entry point for production servers (gunicorn).

Configuration is via env vars so the same image runs in any host:
  DB_PATH               (default: data/full.alt.db)
  SCORING_YAML_PATH     (default: config/scoring.yaml — i.e. canonical)
  FEATURE_OUTREACH      (set to 'true' / '1' to enable, default off)
  WEBAPP_USER           (basic-auth username; absence disables auth)
  WEBAPP_PASSWORD       (basic-auth password; absence disables auth)

Local dev still uses `python -m webapp --db ...` (the argparse CLI in
__main__.py). gunicorn reaches `webapp.wsgi:app` and skips that CLI."""
from __future__ import annotations
import os
from pathlib import Path

from webapp.app import create_app


_db_path = Path(os.environ.get("DB_PATH", "data/full.alt.db"))
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
)
