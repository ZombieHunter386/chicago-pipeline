from __future__ import annotations
import sqlite3
from pathlib import Path
from typing import Any
from flask import Flask, abort, current_app, jsonify, render_template, request

from webapp.filter_schema import build_filter_schema
from webapp.parcel_query import (
    ALLOWED_FILTER_COLUMNS,
    build_count_query,
    build_parcel_query,
)


UI_FILTERS_YAML = Path(__file__).resolve().parent.parent / "config" / "ui_filters.yaml"


def register(app: Flask) -> None:
    @app.get("/")
    def index():
        return render_template(
            "index.html",
            feature_outreach=current_app.config["FEATURE_OUTREACH"],
        )

    @app.get("/api/filters")
    def api_filters():
        schema = build_filter_schema(
            current_app.config["DB_PATH"], UI_FILTERS_YAML
        )
        return jsonify(schema)

    @app.get("/api/parcels")
    def api_parcels():
        filters = _parse_filters(request.args)
        stage = request.args.get("stage") or None
        limit = min(int(request.args.get("limit", 20)), 1000)
        offset = int(request.args.get("offset", 0))

        list_sql, list_params = build_parcel_query(filters, stage, limit, offset)
        count_sql, count_params = build_count_query(filters, stage)

        with _conn() as conn:
            parcels = [dict(r) for r in conn.execute(list_sql, list_params)]
            total = conn.execute(count_sql, count_params).fetchone()["n"]

        return jsonify({"total": total, "parcels": parcels})

    @app.get("/api/parcels/<pin>")
    def api_parcel_detail(pin: str):
        with _conn() as conn:
            row = conn.execute(
                "SELECT * FROM parcels WHERE pin = ?", (pin,)
            ).fetchone()
            if row is None:
                abort(404)
            parcel = dict(row)

            # Attach any contact rows (will be empty in smoke.db)
            contacts = [
                dict(r) for r in conn.execute(
                    "SELECT * FROM contacts WHERE pin = ?", (pin,)
                )
            ]
            parcel["contacts"] = contacts

        parcel["google_maps_url"] = _google_maps_url(parcel)
        return jsonify(parcel)

    @app.get("/api/map-data")
    def api_map_data():
        filters = _parse_filters(request.args)
        stage = request.args.get("stage") or None
        # Map gets up to 5000 pins (all of smoke.db)
        sql, params = build_parcel_query(filters, stage, limit=5000, offset=0)

        with _conn() as conn:
            rows = [dict(r) for r in conn.execute(sql, params)]

        features = []
        for r in rows:
            if r["lat"] is None or r["lng"] is None:
                continue
            features.append({
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [r["lng"], r["lat"]],
                },
                "properties": {
                    "pin": r["pin"],
                    "address": r["address"],
                    "score": r["score"],
                    "category": _map_category(r),
                },
            })

        return jsonify({"type": "FeatureCollection", "features": features})


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(current_app.config["DB_PATH"])
    conn.row_factory = sqlite3.Row
    return conn


def _parse_filters(args) -> dict[str, Any]:
    """Parse query string into the dict shape parcel_query expects.

    Conventions:
      ?is_absentee=true        -> {"is_absentee": True}
      ?property_class=211      -> {"property_class": "211"}
      ?hold_duration_years.min=20  -> {"hold_duration_years": {"min": 20.0}}
      ?hold_duration_years.max=30
    """
    filters: dict[str, Any] = {}
    for key, value in args.items():
        if key in {"limit", "offset", "stage", "sort"}:
            continue

        if "." in key:
            col, suffix = key.split(".", 1)
            if col not in ALLOWED_FILTER_COLUMNS or suffix not in {"min", "max"}:
                continue
            try:
                num = float(value)
            except ValueError:
                continue
            filters.setdefault(col, {})[suffix] = num
            continue

        if key not in ALLOWED_FILTER_COLUMNS:
            continue

        if value.lower() in {"true", "1"}:
            filters[key] = True
        elif value.lower() in {"false", "0"}:
            # Omit — we don't filter "must be false" for checkboxes
            continue
        else:
            filters[key] = value

    return filters


def _map_category(row: dict) -> str:
    """Pin color bucket. Scoring not implemented, so 'top' is never emitted yet."""
    if row.get("listing_status") == "listed":
        return "listed"
    if row.get("stage") == "outreach":
        return "outreach"
    if row.get("consolidation_group_id") is not None:
        return "consolidated"
    return "other"


def _google_maps_url(parcel: dict) -> str:
    if parcel.get("lat") is not None and parcel.get("lng") is not None:
        return f"https://www.google.com/maps?q={parcel['lat']},{parcel['lng']}"
    if parcel.get("address"):
        from urllib.parse import quote_plus
        return f"https://www.google.com/maps?q={quote_plus(parcel['address'] + ', Chicago, IL')}"
    return "https://www.google.com/maps"
