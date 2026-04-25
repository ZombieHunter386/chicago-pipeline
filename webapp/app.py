from __future__ import annotations
from pathlib import Path
from flask import Flask


def create_app(db_path: Path, feature_outreach: bool = False) -> Flask:
    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
    )
    app.config["DB_PATH"] = Path(db_path)
    app.config["FEATURE_OUTREACH"] = feature_outreach

    from webapp import routes
    routes.register(app)
    return app
