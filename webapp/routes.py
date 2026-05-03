from __future__ import annotations
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any
from flask import Flask, abort, current_app, jsonify, render_template, request
from werkzeug.exceptions import HTTPException

from webapp.filter_schema import build_filter_schema
from webapp.parcel_query import (
    ALLOWED_CATEGORIES,
    ALLOWED_FILTER_COLUMNS,
    build_count_query,
    build_parcel_query,
    _build_where,
)


UI_FILTERS_YAML = Path(__file__).resolve().parent.parent / "config" / "ui_filters.yaml"

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 1000
# Bumped from 5000 to 80000 so the user can see every parcel on the map
# (67,677 parcels city-wide; with condo units toggled on, all of them).
# Map.js uses canvas rendering at this scale for performance.
MAP_MAX_PINS = 80000


def _load_top_n() -> int:
    """top_n from scoring.yaml (default 20)."""
    import yaml
    configured = current_app.config.get("SCORING_YAML_PATH")
    if configured:
        yaml_path = Path(configured)
    else:
        yaml_path = (Path(__file__).resolve().parent.parent
                     / "config" / "scoring.yaml")
    if not yaml_path.exists():
        return 20
    try:
        return int(yaml.safe_load(yaml_path.read_text()).get("top_n", 20))
    except Exception:
        return 20


def _resolve_top_n_threshold(conn, filters, stage,
                             include_condo_units: bool,
                             top_n: int) -> float | None:
    """Score at rank top_n WITHIN the filtered population. Computed per
    request so 'Top-N only' respects whatever other filters are active —
    a user filtering by 'is_absentee=Yes' sees the top 20 of the filter
    result, not the global top 20 (which might all be excluded by the
    filter). Returns None when fewer than top_n rows match the filter."""
    from webapp.parcel_query import _build_where
    where_clauses, params = _build_where(filters, stage, include_condo_units)
    where_clauses.append("score IS NOT NULL")
    where_sql = " AND ".join(where_clauses)
    sql = (
        f"SELECT score FROM parcels WHERE {where_sql} "
        f"ORDER BY score DESC LIMIT 1 OFFSET ?"
    )
    row = conn.execute(sql, [*params, max(0, top_n - 1)]).fetchone()
    return row["score"] if row is not None else None


def _parse_visible_categories(arg: str | None) -> set[str] | None:
    """Comma-separated category list; returns None when omitted/empty."""
    if not arg:
        return None
    cats = {c.strip() for c in arg.split(",") if c.strip()}
    cats &= ALLOWED_CATEGORIES
    return cats or None


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

    @app.get("/api/scoring-config")
    def api_scoring_config():
        """Return the active scoring YAML so the UI can render the
        per-signal score breakdown for a selected parcel.

        Resolves the YAML path from app config; falls back to
        config/scoring.yaml relative to the project root."""
        import yaml
        configured = current_app.config.get("SCORING_YAML_PATH")
        if configured:
            yaml_path = Path(configured)
        else:
            yaml_path = (Path(__file__).resolve().parent.parent
                         / "config" / "scoring.yaml")
        if not yaml_path.exists():
            return jsonify({"error": "no scoring config"}), 404
        with yaml_path.open() as f:
            data = yaml.safe_load(f)
        return jsonify(data)

    @app.get("/api/parcels")
    def api_parcels():
        filters = _parse_filters(request.args)
        stage = request.args.get("stage") or None
        include_units = request.args.get("include_condo_units", "").lower() in {"true", "1"}
        top_n_only = request.args.get("top_n_only", "").lower() in {"true", "1"}
        visible_categories = _parse_visible_categories(request.args.get("categories"))
        sort = request.args.get("sort") or None
        direction = request.args.get("dir", "desc")
        try:
            limit = int(request.args.get("limit", DEFAULT_PAGE_SIZE))
            offset = int(request.args.get("offset", 0))
        except ValueError:
            abort(400)
        limit = max(1, min(limit, MAX_PAGE_SIZE))
        offset = max(0, offset)

        with closing(_conn()) as conn:
            top_n = _load_top_n()
            top_n_threshold = _resolve_top_n_threshold(
                conn, filters, stage, include_units, top_n
            )

            try:
                list_sql, list_params = build_parcel_query(
                    filters, stage, limit, offset,
                    include_condo_units=include_units,
                    sort=sort, direction=direction,
                    top_n_only=top_n_only,
                    top_n_threshold=top_n_threshold,
                    visible_categories=visible_categories,
                )
            except ValueError as e:
                abort(400, str(e))
            count_sql, count_params = build_count_query(
                filters, stage, include_condo_units=include_units,
                top_n_only=top_n_only,
                top_n_threshold=top_n_threshold,
                visible_categories=visible_categories,
            )

            parcels = [dict(r) for r in conn.execute(list_sql, list_params)]
            total = conn.execute(count_sql, count_params).fetchone()["n"]

        return jsonify({
            "total": total, "parcels": parcels,
            "top_n": top_n, "top_n_threshold": top_n_threshold,
        })

    @app.get("/api/parcels/<pin>")
    def api_parcel_detail(pin: str):
        # Cook County PINs are 14 digits. Reject anything else outright so
        # we don't run a SQL query (or echo) arbitrary user-supplied strings.
        if not pin.isdigit() or len(pin) != 14:
            abort(404)
        with closing(_conn()) as conn:
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

            # Attach consolidation-group totals when this parcel belongs
            # to a same-owner adjacent group.
            gid = parcel.get("consolidation_group_id")
            if gid is not None:
                grp = conn.execute(
                    "SELECT pins, combined_lot_size_sf, combined_building_sf, owner_name "
                    "FROM consolidation_groups WHERE group_id = ?",
                    (gid,),
                ).fetchone()
                parcel["consolidation_group"] = dict(grp) if grp else None

            # Attach building-SF candidate values from each source so the UI
            # can show 'assessor sum vs largest vs footprint' side-by-side
            # for spot-checking the merge rule on contested parcels.
            chars = conn.execute(
                "SELECT char_bldg_sf, char_bldg_sf_sum, year "
                "FROM raw_assessor_characteristics WHERE pin = ? "
                "ORDER BY year DESC LIMIT 1",
                (pin,),
            ).fetchone()
            parcel["bldg_sf_sources"] = {
                "assessor_largest": chars["char_bldg_sf"] if chars else None,
                "assessor_sum": chars["char_bldg_sf_sum"] if chars else None,
                "current": parcel.get("building_sf"),
                "current_source": parcel.get("building_sf_source"),
            }

        parcel["google_maps_url"] = _google_maps_url(parcel)
        return jsonify(parcel)

    @app.get("/api/map-data")
    def api_map_data():
        filters = _parse_filters(request.args)
        stage = request.args.get("stage") or None
        include_units = request.args.get("include_condo_units", "").lower() in {"true", "1"}
        top_n_only = request.args.get("top_n_only", "").lower() in {"true", "1"}
        visible_categories = _parse_visible_categories(request.args.get("categories"))
        sort = request.args.get("sort") or None
        direction = request.args.get("dir", "desc")

        with closing(_conn()) as conn:
            top_n = _load_top_n()
            top_n_threshold = _resolve_top_n_threshold(
                conn, filters, stage, include_units, top_n
            )
            try:
                sql, params = build_parcel_query(
                    filters, stage, limit=MAP_MAX_PINS, offset=0,
                    include_condo_units=include_units,
                    sort=sort, direction=direction,
                    top_n_only=top_n_only,
                    top_n_threshold=top_n_threshold,
                    visible_categories=visible_categories,
                )
            except ValueError as e:
                abort(400, str(e))

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
                    "category": _map_category(r, top_n_threshold),
                },
            })

        return jsonify({
            "type": "FeatureCollection", "features": features,
            "top_n": top_n, "top_n_threshold": top_n_threshold,
        })

    @app.get("/api/consolidation-groups")
    def api_consolidation_groups():
        """List consolidation groups with summary fields.

        Respects the same filter query string as /api/parcels — a group
        appears in the result only if at least one of its member parcels
        matches the filter (so toggling 'Absentee' or typing 'lincoln'
        in address-search prunes the group list correspondingly).

        Two extra knobs control list noise:

          ?min_combined_lot_size_sf=5000   (default 5000, set 0 to disable)
              Drops tiny groups whose combined lot is below the threshold.
              Most consolidate.py groupings are condo-unit clusters in a
              single building; their combined lot is the building's lot
              counted N times and they aren't usually consolidation plays.

          ?multi_pin10_only=true            (default true, set false to disable)
              Drops groups whose member PINs all share the same pin10 (i.e.
              all units of one building). True consolidation opportunities
              span multiple buildings/lots, which means multiple pin10s.
        """
        filters = _parse_filters(request.args)
        stage = request.args.get("stage") or None
        try:
            min_lot = float(request.args.get("min_combined_lot_size_sf", 5000))
        except ValueError:
            min_lot = 5000.0
        try:
            limit = int(request.args.get("limit", 200))
        except ValueError:
            limit = 200
        limit = max(1, min(limit, 5000))
        multi_pin10_only = request.args.get(
            "multi_pin10_only", "true"
        ).lower() in {"true", "1"}

        try:
            where_clauses, where_params = _build_where(
                filters, stage, include_condo_units=True,
            )
        except ValueError as e:
            abort(400, str(e))
        where_clauses.append("consolidation_group_id IS NOT NULL")
        match_sql = (
            "SELECT DISTINCT consolidation_group_id FROM parcels WHERE "
            + " AND ".join(where_clauses)
        )

        having_clauses = []
        if min_lot > 0:
            having_clauses.append(f"COALESCE(g.combined_lot_size_sf, 0) >= {min_lot}")
        if multi_pin10_only:
            having_clauses.append("COUNT(DISTINCT p.pin10) > 1")
        having_sql = (" HAVING " + " AND ".join(having_clauses)) if having_clauses else ""

        with closing(_conn()) as conn:
            rows = conn.execute(f"""
                SELECT
                    g.group_id, g.pins, g.owner_name, g.detected_date,
                    g.combined_lot_size_sf, g.combined_building_sf,
                    g.score, g.score_version,
                    AVG(p.lat) AS centroid_lat,
                    AVG(p.lng) AS centroid_lng,
                    COUNT(p.pin) AS parcel_count,
                    COUNT(DISTINCT p.pin10) AS distinct_pin10_count,
                    SUM(p.estimated_annual_tax) AS sum_estimated_annual_tax,
                    SUM(p.assessed_total) AS sum_assessed_total,
                    MIN(p.year_built) AS oldest_year_built,
                    MAX(p.hold_duration_years) AS longest_hold_years
                FROM consolidation_groups g
                LEFT JOIN parcels p ON p.consolidation_group_id = g.group_id
                WHERE g.group_id IN ({match_sql})
                GROUP BY g.group_id
                {having_sql}
                ORDER BY g.score DESC NULLS LAST,
                         g.combined_lot_size_sf DESC NULLS LAST
                LIMIT {limit}
            """, where_params).fetchall()
        return jsonify({"groups": [dict(r) for r in rows]})

    @app.get("/api/consolidation-groups/<int:group_id>")
    def api_consolidation_group_detail(group_id: int):
        """Full detail for one group: aggregates + member parcel rows."""
        with closing(_conn()) as conn:
            grp = conn.execute("""
                SELECT
                    g.group_id, g.pins, g.owner_name, g.detected_date,
                    g.combined_lot_size_sf, g.combined_building_sf,
                    AVG(p.lat) AS centroid_lat,
                    AVG(p.lng) AS centroid_lng,
                    COUNT(p.pin) AS parcel_count,
                    SUM(p.estimated_annual_tax) AS sum_estimated_annual_tax,
                    SUM(p.assessed_total) AS sum_assessed_total,
                    MIN(p.year_built) AS oldest_year_built,
                    MAX(p.hold_duration_years) AS longest_hold_years
                FROM consolidation_groups g
                LEFT JOIN parcels p ON p.consolidation_group_id = g.group_id
                WHERE g.group_id = ?
                GROUP BY g.group_id
            """, (group_id,)).fetchone()
            if grp is None:
                abort(404)
            members = [dict(r) for r in conn.execute("""
                SELECT pin, address, lat, lng, property_class, lot_size_sf,
                       building_sf, year_built, assessed_total,
                       estimated_annual_tax, hold_duration_years,
                       zone_class, max_far, built_far, far_gap, far_gap_delta,
                       allows_multifamily_by_right, min_lot_area_per_unit,
                       max_units_allowed
                FROM parcels
                WHERE consolidation_group_id = ?
                ORDER BY pin
            """, (group_id,)).fetchall()]
        body = dict(grp)
        body["members"] = members
        body["zoning_summary"] = _summarize_zoning(members, body)
        return jsonify(body)

    @app.get("/_test_explode")
    def _test_explode():
        # Only available in test mode; production gets a normal 404.
        if not current_app.config.get("TESTING"):
            abort(404)
        raise RuntimeError("boom")

    @app.errorhandler(Exception)
    def handle_unexpected(e):
        # Let Flask's default behavior produce HTTP errors (404, 400, etc.)
        # so they keep their proper status codes and bodies.
        if isinstance(e, HTTPException):
            return e
        app.logger.exception("Unhandled error in %s", request.path)
        return jsonify({"error": "internal_error"}), 500


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(current_app.config["DB_PATH"])
    conn.row_factory = sqlite3.Row
    return conn


def _parse_filters(args) -> dict[str, Any]:
    """Parse query string into the dict shape parcel_query expects.

    Conventions:
      ?is_absentee=true                         -> {"is_absentee": True}
      ?property_class=211                       -> {"property_class": "211"}
      ?property_class=211&property_class=212    -> {"property_class": ["211","212"]}
      ?hold_duration_years.min=20               -> {"hold_duration_years": {"min": 20.0}}
      ?hold_duration_years.max=30
    """
    filters: dict[str, Any] = {}
    seen_keys: set[str] = set()
    for key in args.keys():
        if key in seen_keys:
            continue
        seen_keys.add(key)
        if key in {"limit", "offset", "stage", "sort", "dir", "include_condo_units"}:
            continue

        if "." in key:
            col, suffix = key.split(".", 1)
            if col not in ALLOWED_FILTER_COLUMNS or suffix not in {"min", "max"}:
                continue
            try:
                num = float(args.get(key))
            except (ValueError, TypeError):
                continue
            filters.setdefault(col, {})[suffix] = num
            continue

        if key not in ALLOWED_FILTER_COLUMNS:
            continue

        values = args.getlist(key)
        if len(values) > 1:
            filters[key] = [v for v in values if v != ""]
            continue

        value = values[0]
        if value.lower() in {"true", "1"}:
            filters[key] = True
        elif value.lower() in {"false", "0"}:
            filters[key] = False
        else:
            filters[key] = value

    return filters


def _map_category(row: dict, top_n_threshold: float | None = None) -> str:
    """Pin color bucket. 'top' fires when the row's score is at or above
    the top-N threshold (default: top 20 by score across the parcels table).
    Bucket precedence: outreach > consolidated > top > other. Listed parcels
    are surfaced separately in the outreach stage.

    Condo buildings (is_condo_building=1) fall in the 'consolidated' bucket
    alongside owner-portfolio groups so the user only has to remember one
    rollup concept."""
    if row.get("listing_status") == "listed":
        return "listed"
    if row.get("stage") == "outreach":
        return "outreach"
    if (row.get("consolidation_group_id") is not None
            or row.get("is_condo_building")):
        return "consolidated"
    score = row.get("score")
    if (top_n_threshold is not None and score is not None
            and score >= top_n_threshold):
        return "top"
    return "other"


def _summarize_zoning(members: list, group: dict) -> dict:
    """Aggregate the zoning fields across member parcels of a consolidation
    group. When all members share a zone, the result reads like a single
    parcel; when they differ, we surface a per-zone breakdown plus a
    'dominant zone' (most-common, ties broken by largest combined lot SF
    in that zone) under which combined-development potential is computed."""
    from collections import defaultdict

    zone_buckets: dict = defaultdict(lambda: {
        "parcel_count": 0,
        "lot_sf": 0.0,
        "max_far": None,
        "min_lot_area_per_unit": None,
        "allows_multifamily_by_right": None,
    })
    for m in members:
        zc = m.get("zone_class") or "(unknown)"
        b = zone_buckets[zc]
        b["parcel_count"] += 1
        b["lot_sf"] += m.get("lot_size_sf") or 0.0
        # Per-zone constants: copy from any member with a non-null value.
        for key in ("max_far", "min_lot_area_per_unit", "allows_multifamily_by_right"):
            if b[key] is None and m.get(key) is not None:
                b[key] = m[key]

    breakdown = [
        {"zone_class": zc, **vals} for zc, vals in zone_buckets.items()
    ]
    breakdown.sort(key=lambda r: (-r["parcel_count"], -r["lot_sf"]))

    is_uniform = len([z for z in zone_buckets if z != "(unknown)"]) == 1
    dominant = breakdown[0]["zone_class"] if breakdown else None
    dominant_max_far = breakdown[0]["max_far"] if breakdown else None
    dominant_min_lot_pu = breakdown[0]["min_lot_area_per_unit"] if breakdown else None

    # Combined-lot development potential under the dominant zone.
    combined_lot = group.get("combined_lot_size_sf") or 0.0
    combined_bldg = group.get("combined_building_sf") or 0.0
    combined_built_far = (
        round(combined_bldg / combined_lot, 4) if combined_lot > 0 and combined_bldg > 0 else None
    )
    combined_max_buildable_sf = (
        round(combined_lot * dominant_max_far, 0) if combined_lot and dominant_max_far else None
    )
    combined_far_gap_delta = (
        round(dominant_max_far - combined_built_far, 4)
        if dominant_max_far is not None and combined_built_far is not None
        else None
    )
    combined_max_units = (
        int(combined_lot // dominant_min_lot_pu)
        if combined_lot and dominant_min_lot_pu and dominant_min_lot_pu > 0
        else None
    )

    # Multifamily-by-right: aggregate yes/no across members
    yes = sum(1 for b in zone_buckets.values() if b["allows_multifamily_by_right"] == 1)
    no = sum(1 for b in zone_buckets.values() if b["allows_multifamily_by_right"] == 0)
    if yes > 0 and no == 0:
        mf_status = "all"
    elif no > 0 and yes == 0:
        mf_status = "none"
    elif yes > 0 and no > 0:
        mf_status = "mixed"
    else:
        mf_status = "unknown"

    return {
        "is_uniform_zone": is_uniform,
        "dominant_zone": dominant,
        "breakdown": breakdown,
        "combined_built_far": combined_built_far,
        "combined_max_buildable_sf": combined_max_buildable_sf,
        "combined_far_gap_delta": combined_far_gap_delta,
        "combined_max_units_dominant_zone": combined_max_units,
        "allows_multifamily_status": mf_status,
    }


def _google_maps_url(parcel: dict) -> str:
    if parcel.get("lat") is not None and parcel.get("lng") is not None:
        return f"https://www.google.com/maps?q={parcel['lat']},{parcel['lng']}"
    if parcel.get("address"):
        from urllib.parse import quote_plus
        return f"https://www.google.com/maps?q={quote_plus(parcel['address'] + ', Chicago, IL')}"
    return "https://www.google.com/maps"
