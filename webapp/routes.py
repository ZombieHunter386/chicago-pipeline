from __future__ import annotations
from flask import Flask, current_app, render_template


def register(app: Flask) -> None:
    @app.get("/")
    def index():
        return render_template(
            "index.html",
            feature_outreach=current_app.config["FEATURE_OUTREACH"],
        )
