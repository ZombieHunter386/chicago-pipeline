from __future__ import annotations
from pathlib import Path
from flask import Flask


def create_app(
    db_path: Path,
    feature_outreach: bool = False,
    scoring_yaml_path: Path | None = None,
    outreach_templates_path: Path | None = None,
    gmail_client_secrets_path: Path | None = None,
    gmail_token_path: Path | None = None,
    gmail_sender_address: str | None = None,
) -> Flask:
    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
    )
    app.config["DB_PATH"] = Path(db_path)
    app.config["FEATURE_OUTREACH"] = feature_outreach
    app.config["SCORING_YAML_PATH"] = (
        Path(scoring_yaml_path) if scoring_yaml_path else None
    )
    app.config["OUTREACH_TEMPLATES_PATH"] = outreach_templates_path or (
        Path(__file__).resolve().parent.parent / "config" / "outreach_templates.yaml"
    )
    app.config["GMAIL_CLIENT_SECRETS_PATH"] = gmail_client_secrets_path or Path(
        "data/gmail_oauth_client.json"
    )
    app.config["GMAIL_TOKEN_PATH"] = gmail_token_path or Path("data/gmail_token.json")
    app.config["GMAIL_SENDER_ADDRESS"] = gmail_sender_address or ""

    from webapp import routes
    routes.register(app)

    # HTTP basic auth — activated when WEBAPP_USER + WEBAPP_PASSWORD env
    # vars are set (production). No-op in local dev when env vars are absent.
    from webapp.auth import init_auth
    init_auth(app)

    return app
