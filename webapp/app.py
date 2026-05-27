from __future__ import annotations
from datetime import date
from pathlib import Path
from typing import Callable
from flask import Flask


def create_app(
    db_path: Path,
    feature_outreach: bool = False,
    scoring_yaml_path: Path | None = None,
    outreach_templates_path: Path | None = None,
    outreach_cadence_path: Path | None = None,
    clock: "Callable[[], date] | None" = None,
    gmail_client_secrets_path: Path | None = None,
    gmail_token_path: Path | None = None,
    gmail_sender_address: str | None = None,
    esri_api_key: str | None = None,
    due_digest_last_run_path: Path | None = None,
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
    app.config["OUTREACH_CADENCE_PATH"] = outreach_cadence_path or (
        Path(__file__).resolve().parent.parent / "config" / "outreach_cadence.yaml"
    )
    # Clock dependency: production code calls app.config["CLOCK"]() to get
    # today's date. Tests override this to pin a specific date — no URL
    # parameter needed, no production "?today=" surface.
    app.config["CLOCK"] = clock or date.today
    app.config["GMAIL_CLIENT_SECRETS_PATH"] = gmail_client_secrets_path or Path(
        "data/gmail_oauth_client.json"
    )
    app.config["GMAIL_TOKEN_PATH"] = gmail_token_path or Path("data/gmail_token.json")
    app.config["GMAIL_SENDER_ADDRESS"] = gmail_sender_address or ""
    # Esri ibasemaps API key — opt-in. When set, the satellite basemap uses
    # Esri's API-keyed World_Imagery endpoint (no anonymous quota wall).
    # Without it, map.js falls back to the anonymous URL (fine for hobby
    # local dev; hits "Account Limit Exceeded" under deployed traffic).
    app.config["ESRI_API_KEY"] = esri_api_key or ""
    # Default is None — the health endpoint treats that as "no observability
    # configured" rather than reading a real path. Tests and prod each set
    # their own path explicitly. Avoids relative-path test pollution.
    app.config["DUE_DIGEST_LAST_RUN_PATH"] = due_digest_last_run_path

    from webapp import routes
    routes.register(app)

    # HTTP basic auth — activated when WEBAPP_USER + WEBAPP_PASSWORD env
    # vars are set (production). No-op in local dev when env vars are absent.
    from webapp.auth import init_auth
    init_auth(app)

    return app
