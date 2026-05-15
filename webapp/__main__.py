from __future__ import annotations
import argparse
from pathlib import Path
from webapp.app import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="Chicago Pipeline Review UI")
    parser.add_argument("--db", type=Path, default=Path("data/smoke.db"),
                        help="Path to SQLite database (default: data/smoke.db)")
    parser.add_argument("--scoring-yaml", type=Path, default=None,
                        help="Path to scoring YAML for the score-breakdown panel "
                             "(default: config/scoring.yaml relative to project root)")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--outreach", action="store_true",
                        help="Enable outreach UI + write endpoints (local only)")
    parser.add_argument("--debug", action="store_true",
                        help="Enable Flask debug mode (exposes Werkzeug debugger; localhost only)")
    args = parser.parse_args()

    if not args.db.exists():
        raise SystemExit(f"Database not found: {args.db}")

    # Outreach config reads from env so a developer can override paths without
    # touching code. All env vars are optional; defaults are baked into create_app.
    import os
    gmail_client = os.environ.get("GMAIL_OAUTH_CLIENT_PATH")
    gmail_token = os.environ.get("GMAIL_TOKEN_PATH")
    gmail_sender = os.environ.get("GMAIL_SENDER_ADDRESS")

    if args.outreach:
        # oauthlib refuses to do OAuth over plain HTTP by default; the local
        # dev server is http://localhost:5051 — never reachable from outside
        # this machine. Allow insecure transport only when --outreach is on
        # (which is itself a local-only flag; the wsgi.py prod entry refuses
        # to enable outreach even if the env var slips through).
        os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")

    app = create_app(
        db_path=args.db, feature_outreach=args.outreach,
        scoring_yaml_path=args.scoring_yaml,
        gmail_client_secrets_path=Path(gmail_client) if gmail_client else None,
        gmail_token_path=Path(gmail_token) if gmail_token else None,
        gmail_sender_address=gmail_sender,
    )
    app.run(host="127.0.0.1", port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
