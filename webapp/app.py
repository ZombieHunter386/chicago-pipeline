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
    profile_defaults_path: Path | None = None,
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
    app.config["PROFILE_DEFAULTS_PATH"] = profile_defaults_path or (
        Path(__file__).resolve().parent.parent / "config" / "profile_defaults.yaml"
    )

    # Enrichment provider wiring — reads config/enrichment.yaml + env for the
    # provider API keys, hands the constructed providers to the routes via
    # app.config so tests can inject stubs.
    import yaml
    enrichment_cfg_path = Path("config/enrichment.yaml")
    if enrichment_cfg_path.exists() and feature_outreach:
        enrichment_cfg = yaml.safe_load(enrichment_cfg_path.read_text())
        app.config["ENRICHMENT_CFG"] = enrichment_cfg
        import os
        tracerfy_key = os.environ.get("TRACERFY_API_KEY")
        if tracerfy_key:
            from pipeline.enrichment_providers.tracerfy import TracerfyProvider
            app.config["ENRICHMENT_SKIP_PROVIDER"] = TracerfyProvider(api_key=tracerfy_key)
        from pipeline.enrichment import BudgetCap
        budget_cfg = enrichment_cfg.get("budget", {})
        app.config["ENRICHMENT_BUDGET"] = BudgetCap(
            soft_daily_usd=float(budget_cfg.get("soft_daily_usd", 5.00)),
            hard_per_run_usd=float(budget_cfg.get("hard_per_run_usd", 2.50)),
        )

    # Static-asset cache-busting: append ?v=<mtime> to /static URLs so the
    # browser sees a new URL whenever a JS/CSS file changes. Flask sends
    # Cache-Control: no-cache + ETag already, but Chrome/Safari occasionally
    # serve stale copies anyway. A query-string version is the bulletproof
    # fix — different URL == guaranteed cache miss.
    def static_v(filename: str) -> str:
        try:
            return str(int(
                (Path(app.static_folder) / filename).stat().st_mtime
            ))
        except OSError:
            return "0"
    app.jinja_env.globals["static_v"] = static_v

    from webapp import routes
    routes.register(app)

    # HTTP basic auth — activated when WEBAPP_USER + WEBAPP_PASSWORD env
    # vars are set (production). No-op in local dev when env vars are absent.
    from webapp.auth import init_auth
    init_auth(app)

    return app
