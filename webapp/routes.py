from __future__ import annotations
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any
from flask import Flask, abort, current_app, jsonify, redirect, render_template, request, url_for
from werkzeug.exceptions import HTTPException

from google.auth.exceptions import RefreshError
from googleapiclient.errors import HttpError

from pipeline import outreach as outreach_module, gmail_client
from pipeline import cadence as cadence_module
from webapp.filter_schema import build_filter_schema
from webapp.parcel_query import (
    ALLOWED_CATEGORIES,
    ALLOWED_FILTER_COLUMNS,
    build_count_query,
    build_parcel_query,
    _build_where,
)


UI_FILTERS_YAML = Path(__file__).resolve().parent.parent / "config" / "ui_filters.yaml"

from pipeline.profile_defaults import load_profile_defaults as _load_profile_defaults_raw


@lru_cache(maxsize=4)
def _load_profile_defaults_cached(path_str: str) -> dict:
    """Parse profile_defaults.yaml once per path and cache the result.

    YAML parsing is cheap but there's no reason to re-read the file on every
    /api/parcels and /api/profile-defaults request — the file only changes
    when the operator edits it (which requires a server restart anyway).
    """
    return _load_profile_defaults_raw(Path(path_str))


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
            esri_api_key=current_app.config.get("ESRI_API_KEY", ""),
        )

    @app.get("/health")
    def health():
        # Unauthenticated liveness probe for Render's health checker (the
        # auth middleware whitelists this path). Plain text, no DB hit, so
        # it stays cheap and never gets blocked by a slow query.
        return "ok", 200, {"Content-Type": "text/plain"}

    @app.get("/api/filters")
    def api_filters():
        schema = build_filter_schema(
            current_app.config["DB_PATH"], UI_FILTERS_YAML
        )
        return jsonify(schema)

    @app.get("/api/profile-defaults")
    def api_profile_defaults():
        """Return the profile registry: {profile_name: {score_column,
        recommended_filters, ...}}. The UI uses this to populate the
        profile dropdown + auto-apply recommended filters when the
        operator picks a profile."""
        out = _load_profile_defaults_cached(str(app.config["PROFILE_DEFAULTS_PATH"]))
        return jsonify(out)

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

        # Profile selection: ?profile=<name> picks which score column to sort by.
        # Resolves via profile_defaults.yaml registry; unknown profile → 400.
        profile_param = request.args.get("profile")
        if profile_param:
            profiles = _load_profile_defaults_cached(str(app.config["PROFILE_DEFAULTS_PATH"]))
            if profile_param not in profiles:
                abort(400, f"unknown profile: {profile_param}")
            sort = profiles[profile_param]["score_column"]
            direction = "desc"
        else:
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

    # ============================================================
    # Outreach (Plan 4) — registered only when FEATURE_OUTREACH is on
    # ============================================================
    if app.config["FEATURE_OUTREACH"]:

        EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
        ALLOWED_STAGES = {"scored", "outreach", "responded", "introduced", "dead"}
        ALLOWED_RESPONSE_TYPES = {"responded", "not_interested", "wrong_owner", "other"}

        def _now_iso() -> str:
            return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        def _load_outreach_config() -> dict:
            return outreach_module.load_templates(
                Path(app.config["OUTREACH_TEMPLATES_PATH"])
            )

        def _today():
            """Returns the current date via the injected clock. Tests
            override app.config["CLOCK"] to a fixed lambda."""
            return app.config["CLOCK"]()

        def _load_cadence():
            return cadence_module.load_cadence_config(
                Path(app.config["OUTREACH_CADENCE_PATH"])
            )

        def _parcel_or_404(conn, pin: str):
            if not pin.isdigit() or len(pin) != 14:
                abort(404)
            row = conn.execute(
                "SELECT * FROM parcels WHERE pin = ?", (pin,)
            ).fetchone()
            if row is None:
                abort(404)
            return dict(row)

        @app.get("/api/parcels/<pin>/outreach")
        def api_parcel_outreach(pin: str):
            with closing(_conn()) as conn:
                parcel = _parcel_or_404(conn, pin)
                # All contact rows for the parcel — T3 dropped the LIMIT 1
                # single-contact assumption; multi-row contacts (post-enrichment)
                # are now the norm and the cadence engine filters dead rows.
                contact_rows = conn.execute(
                    "SELECT * FROM contacts WHERE pin = ?", (pin,)
                ).fetchall()
                contacts = [dict(r) for r in contact_rows]
                outreach_rows = outreach_module.list_outreach_for_parcel(conn, pin)
                outreach_dicts = [dict(r) for r in outreach_rows]

            # Compute sequence state
            cadence = _load_cadence()
            today = _today()
            by_touch = {
                r["touch_number"]: r for r in outreach_dicts
                if r.get("touch_number") is not None
            }
            anchor_row = by_touch.get(1)
            anchor_date = (
                anchor_row["sent_date"][:10]
                if anchor_row and anchor_row.get("sent_date") else None
            )
            current_touch = max(by_touch.keys()) if by_touch else 0
            is_paused = bool(parcel.get("outreach_paused"))
            is_eos = cadence_module.is_end_of_sequence(
                cadence_config=cadence, outreach_rows=outreach_dicts, today=today,
            )

            next_due = None
            if not is_paused:
                due_list = cadence_module.next_due_touches_for_parcel(
                    cadence_config=cadence,
                    outreach_rows=outreach_dicts,
                    contacts=contacts,
                    parcel_mail_address=parcel.get("mail_address"),
                    today=today,
                )
                if due_list:
                    n = due_list[0]
                    next_due = {
                        "touch": n["touch"],
                        "channel": n["channel"],
                        "target_date": n["target_date"],
                        "days_overdue": n["days_overdue"],
                        "available": True,
                    }

            # Always-available "next unsent touch" — the next sequence step
            # the operator could send right now, ignoring cadence dates.
            # Lets the UI offer a "send ahead of schedule" path when
            # next_due is null but we're not at end-of-sequence. The check
            # mirrors next_due's `available` semantics: the channel's
            # required contact field (email / phone / mail_address) must
            # be present on at least one alive contact.
            next_unsent = None
            if not is_eos:
                sent_touch_nums = {
                    r["touch_number"] for r in outreach_dicts
                    if r.get("touch_number") is not None
                }
                alive_contacts = [
                    c for c in contacts
                    if not c.get("dead") and not c.get("wrong_person")
                ]
                has_email = any(c.get("email") for c in alive_contacts)
                has_phone = any(c.get("phone") for c in alive_contacts)
                has_mail_addr = bool(parcel.get("mail_address"))
                for t in sorted(cadence["sequence"], key=lambda x: x["touch"]):
                    if t["touch"] in sent_touch_nums:
                        continue
                    requires = t.get("requires")
                    available = (
                        has_email if requires == "email"
                        else has_phone if requires == "phone"
                        else has_mail_addr if requires == "mail_address"
                        else True
                    )
                    next_unsent = {
                        "touch": t["touch"],
                        "channel": t["channel"],
                        "available": available,
                    }
                    break

            # Response shape: keep the legacy single `contact` field for any
            # callers still reading it, and add `contacts` (plural) which the
            # T11 multi-row UI consumes. `contact` mirrors contacts[0] so old
            # paths keep working during the T11→T13 transition.
            primary_contact = contacts[0] if contacts else None
            return jsonify({
                "pin": pin,
                "contact": primary_contact,
                "contacts": contacts,
                "outreach": outreach_dicts,
                "gmail_connected": gmail_client.is_connected(
                    Path(app.config["GMAIL_TOKEN_PATH"])
                ),
                "sender_address": app.config.get("GMAIL_SENDER_ADDRESS") or "",
                "sequence": {
                    "anchor_date": anchor_date,
                    "current_touch": current_touch,
                    "next_due": next_due,
                    "next_unsent": next_unsent,
                    "is_end_of_sequence": is_eos,
                    "is_paused": is_paused,
                },
            })

        @app.get("/api/outreach/templates")
        def api_outreach_templates():
            cfg = _load_outreach_config()
            pin = request.args.get("pin")
            templates = []
            ctx = {}
            if pin and pin.isdigit() and len(pin) == 14:
                with closing(_conn()) as conn:
                    row = conn.execute(
                        "SELECT * FROM parcels WHERE pin = ?", (pin,)
                    ).fetchone()
                    if row is not None:
                        ctx = outreach_module.parcel_context(dict(row), cfg["defaults"])
            for name, tpl in cfg["templates"].items():
                templates.append({
                    "name": name,
                    "label": tpl.get("label", name),
                    "subject": tpl.get("subject", ""),
                    "body": tpl.get("body", ""),
                    "rendered_subject": outreach_module.render_template(
                        tpl.get("subject", ""), ctx
                    ) if ctx else None,
                    "rendered_body": outreach_module.render_template(
                        tpl.get("body", ""), ctx
                    ) if ctx else None,
                })
            return jsonify({"templates": templates})

        @app.post("/api/outreach/templates/save")
        def api_outreach_save_template():
            data = request.get_json(silent=True) or {}
            name = (data.get("name") or "").strip()
            subject = data.get("subject")
            body = data.get("body")
            label = data.get("label")  # optional
            if not name:
                abort(400, "name is required")
            if not subject:
                abort(400, "subject is required")
            if body is None:
                abort(400, "body is required")
            # Names limited to a safe set — prevents weirdness in file content
            # and accidental UI breakage from special characters.
            if not re.match(r"^[A-Za-z0-9][A-Za-z0-9 _-]{0,63}$", name):
                abort(400, "invalid name: use letters, numbers, spaces, _ or - (max 64 chars)")
            try:
                saved = outreach_module.save_template(
                    Path(app.config["OUTREACH_TEMPLATES_PATH"]),
                    name=name, subject=subject, body=body, label=label,
                )
            except OSError as e:
                abort(500, f"failed to save template: {e}")
            return jsonify({"template": saved})

        @app.get("/api/outreach/due")
        def api_outreach_due():
            cadence = _load_cadence()
            with closing(_conn()) as conn:
                return jsonify(
                    cadence_module.all_due_touches(conn, cadence, _today())
                )

        @app.get("/api/cadence/config")
        def api_cadence_config():
            return jsonify(_load_cadence())

        @app.get("/api/health/digest")
        def api_health_digest():
            """Returns the last-known-good timestamp of the daily digest
            cron + a stale flag. Used by the UI to warn when the digest
            hasn't fired (Mac was off, cron is broken, etc.). When the
            config has no sentinel path set (e.g., in tests with no
            override), reports as not-configured rather than crashing."""
            raw = app.config.get("DUE_DIGEST_LAST_RUN_PATH")
            if raw is None:
                return jsonify({"last_run": None, "stale": True,
                                "reason": "not configured"})
            p = Path(raw)
            if not p.exists():
                return jsonify({"last_run": None, "stale": True,
                                "reason": "no sentinel file yet"})
            try:
                ts_text = p.read_text().strip()
                ts = datetime.fromisoformat(ts_text.replace("Z", "+00:00"))
            except (ValueError, OSError):
                return jsonify({"last_run": None, "stale": True,
                                "reason": "unparseable sentinel"})
            stale = (datetime.now(timezone.utc) - ts) > timedelta(hours=25)
            return jsonify({"last_run": ts_text, "stale": stale})

        @app.post("/api/outreach/log-manual-touch")
        def api_outreach_log_manual_touch():
            data = request.get_json(silent=True) or {}
            pin = data.get("pin") or ""
            touch_number = data.get("touch_number")
            channel = data.get("channel")
            notes = data.get("notes") or ""
            if not pin.isdigit() or len(pin) != 14:
                abort(400, "invalid pin")
            if not isinstance(touch_number, int):
                abort(400, "touch_number must be an integer")
            if channel not in ("phone", "mail", "skipped"):
                abort(400, "channel must be 'phone', 'mail', or 'skipped'")

            cadence = _load_cadence()
            tpl = next(
                (t for t in cadence["sequence"] if t["touch"] == touch_number),
                None,
            )
            if tpl is None:
                abort(400, f"unknown touch_number {touch_number}")
            # 'skipped' is always allowed regardless of the cadence's configured
            # channel for this touch — the user chose not to do it.
            if channel != "skipped" and tpl["channel"] != channel:
                abort(
                    400,
                    f"touch {touch_number} channel is "
                    f"{tpl['channel']!r}, not {channel!r}",
                )

            with closing(_conn()) as conn:
                _parcel_or_404(conn, pin)
                outreach_rows = [
                    dict(r) for r in conn.execute(
                        "SELECT * FROM outreach WHERE pin = ? "
                        "ORDER BY touch_number",
                        (pin,),
                    )
                ]
                try:
                    outreach_module.validate_next_due_touch(
                        outreach_rows=outreach_rows,
                        touch_number=touch_number,
                    )
                except ValueError as e:
                    abort(400, str(e))
                try:
                    oid = outreach_module.create_outreach_record(
                        conn, pin=pin, contact_id=None,
                        channel=channel, subject="",
                        body=notes,
                        sent_date=_now_iso(),
                        touch_number=touch_number,
                    )
                except sqlite3.IntegrityError:
                    abort(409, "touch already completed")
                if notes:
                    # create_outreach_record writes body into draft_body+final_body,
                    # not the dedicated notes column — separate UPDATE to keep
                    # the helper signature clean.
                    conn.execute(
                        "UPDATE outreach SET notes = ? WHERE outreach_id = ?",
                        (notes, oid),
                    )
                    conn.commit()
                # Compute next due so the UI can update without a refetch.
                outreach_rows.append({
                    "touch_number": touch_number,
                    "sent_date": _now_iso(),
                })
                # T3: fetch all contact rows; cadence engine applies the
                # per-row alive filter (dead=0 AND wrong_person=0).
                contact_rows = conn.execute(
                    "SELECT * FROM contacts WHERE pin = ?", (pin,)
                ).fetchall()
                contacts = [dict(r) for r in contact_rows]
                parcel_row = conn.execute(
                    "SELECT mail_address FROM parcels WHERE pin = ?", (pin,)
                ).fetchone()
                due = cadence_module.next_due_touches_for_parcel(
                    cadence_config=cadence,
                    outreach_rows=outreach_rows,
                    contacts=contacts,
                    parcel_mail_address=parcel_row["mail_address"],
                    today=_today(),
                )
                next_touch = due[0] if due else None

            return jsonify({"outreach_id": oid, "next_touch": next_touch})

        @app.post("/api/parcels/<pin>/pause")
        def api_parcel_pause(pin: str):
            data = request.get_json(silent=True) or {}
            paused = bool(data.get("paused"))
            if not pin.isdigit() or len(pin) != 14:
                abort(400, "invalid pin")
            with closing(_conn()) as conn:
                _parcel_or_404(conn, pin)
                conn.execute(
                    "UPDATE parcels SET outreach_paused = ? WHERE pin = ?",
                    (1 if paused else 0, pin),
                )
                conn.commit()
            return jsonify({"pin": pin, "paused": paused})

        @app.post("/api/contacts/upsert")
        def api_contacts_upsert():
            data = request.get_json(silent=True) or {}
            pin = data.get("pin") or ""
            email = data.get("email")
            if not pin.isdigit() or len(pin) != 14:
                abort(400, "invalid pin")
            if email is not None and not EMAIL_RE.match(email):
                abort(400, "invalid email")
            with closing(_conn()) as conn:
                _parcel_or_404(conn, pin)
                cid = outreach_module.upsert_contact(
                    conn, pin=pin,
                    email=email,
                    name=data.get("name"),
                    phone=data.get("phone"),
                    role=data.get("role"),
                    source=data.get("source", "manual"),
                )
                row = conn.execute(
                    "SELECT * FROM contacts WHERE contact_id = ?", (cid,)
                ).fetchone()
            return jsonify({"contact": dict(row)})

        @app.post("/api/enrichment/lookup/<pin>")
        def api_enrichment_lookup(pin: str):
            if not pin.isdigit() or len(pin) != 14:
                abort(400, "invalid pin")
            provider = app.config.get("ENRICHMENT_SKIP_PROVIDER")
            budget = app.config.get("ENRICHMENT_BUDGET")
            if not provider:
                abort(503, "Enrichment provider not configured (set TRACERFY_API_KEY)")
            with closing(_conn()) as conn:
                parcel = conn.execute(
                    "SELECT * FROM parcels WHERE pin=?", (pin,)
                ).fetchone()
                if parcel is None:
                    abort(404)
                from pipeline.enrichment import _has_fresh_contacts, _enrich_one_pin
                if _has_fresh_contacts(conn, pin):
                    abort(409, "parcel already has contacts (no auto re-enrich)")
                # Single-parcel lookup uses the soft daily cap only — the hard
                # per-run cap is bulk-job-scoped. If over soft and the user
                # hasn't passed ?confirm=true, surface 429 so the UI can prompt.
                if budget.would_exceed_soft(
                    conn, additional_cost=provider.cost_per_lookup_usd,
                ) and request.args.get("confirm") != "true":
                    abort(429, "soft daily cap would be exceeded; resend with ?confirm=true to override")
                try:
                    result = _enrich_one_pin(
                        conn, job_id=None, pin=pin, provider=provider,
                    )
                    conn.commit()
                except Exception as e:
                    abort(500, str(e))
                # Provider returned an error row (e.g. Tracerfy 400 on a
                # malformed payload). The error is already persisted to
                # enrichment_results for audit; surface 502 to the UI so
                # operators see what went wrong instead of an empty list.
                if result.status == "error":
                    abort(502, result.error_message or "provider error")
                rows = [dict(r) for r in conn.execute(
                    "SELECT * FROM contacts WHERE pin=?", (pin,)
                )]
            return jsonify({"status": "success", "contacts": rows})

        @app.post("/api/enrichment/bulk")
        def api_enrichment_bulk():
            data = request.get_json(silent=True) or {}
            pins = data.get("pins") or []
            if not isinstance(pins, list) or not pins:
                abort(400, "pins must be a non-empty list")
            for p in pins:
                if not (isinstance(p, str) and p.isdigit() and len(p) == 14):
                    abort(400, f"invalid pin: {p}")
            provider = app.config.get("ENRICHMENT_SKIP_PROVIDER")
            budget = app.config.get("ENRICHMENT_BUDGET")
            if not provider:
                abort(503, "Enrichment provider not configured (set TRACERFY_API_KEY)")
            db_path = Path(app.config["DB_PATH"])
            def conn_factory():
                c = sqlite3.connect(db_path)
                c.row_factory = sqlite3.Row
                return c
            from pipeline.enrichment import create_enrichment_job, run_bulk_enrichment
            with closing(conn_factory()) as conn:
                job_id = create_enrichment_job(conn, pins)
                conn.commit()
            import threading
            threading.Thread(
                target=run_bulk_enrichment,
                kwargs=dict(conn_factory=conn_factory, job_id=job_id, pin_list=pins,
                            provider=provider, budget=budget),
                daemon=True,
            ).start()
            return jsonify({"job_id": job_id}), 202

        @app.get("/api/enrichment/job/<int:job_id>")
        def api_enrichment_job_status(job_id: int):
            with closing(_conn()) as conn:
                job = conn.execute(
                    "SELECT * FROM enrichment_jobs WHERE id=?", (job_id,)
                ).fetchone()
                if job is None:
                    abort(404)
                pins = conn.execute(
                    "SELECT pin, status, error_message FROM enrichment_job_pins WHERE job_id=?",
                    (job_id,)
                ).fetchall()
            return jsonify({
                "job_id": job_id,
                "status": job["status"],
                "paused_reason": job["paused_reason"],
                "total_cost_usd": job["total_cost_usd"],
                "pins": [dict(p) for p in pins],
            })

        @app.post("/api/contacts/<int:contact_id>/dead")
        def api_contact_mark_dead(contact_id: int):
            with closing(_conn()) as conn:
                cur = conn.execute(
                    "UPDATE contacts SET dead=1, "
                    "dead_at=strftime('%Y-%m-%dT%H:%M:%SZ', 'now'), "
                    "dead_reason='manual' WHERE contact_id=?",
                    (contact_id,),
                )
                if cur.rowcount == 0:
                    abort(404)
                conn.commit()
            return jsonify({"ok": True})

        @app.post("/api/contacts/<int:contact_id>/wrong-person")
        def api_contact_mark_wrong_person(contact_id: int):
            with closing(_conn()) as conn:
                cur = conn.execute(
                    "UPDATE contacts SET wrong_person=1 WHERE contact_id=?",
                    (contact_id,),
                )
                if cur.rowcount == 0:
                    abort(404)
                conn.commit()
            return jsonify({"ok": True})

        @app.post("/api/contacts/<int:contact_id>/update")
        def api_contact_update(contact_id: int):
            """Update a specific contact row's email or phone value. Used by
            the UI's Edit button to replace the value without going through
            upsert semantics (which dedupe by pin+value and would create a
            new row instead of editing in place)."""
            data = request.get_json(silent=True) or {}
            email = data.get("email")
            phone = data.get("phone")
            if email is None and phone is None:
                abort(400, "email or phone is required")
            if email is not None and not EMAIL_RE.match(email):
                abort(400, "invalid email")
            with closing(_conn()) as conn:
                sets = []
                params: list = []
                if email is not None:
                    sets.append("email = ?")
                    params.append(email)
                if phone is not None:
                    sets.append("phone = ?")
                    params.append(phone)
                params.append(contact_id)
                cur = conn.execute(
                    f"UPDATE contacts SET {', '.join(sets)} WHERE contact_id = ?",
                    params,
                )
                if cur.rowcount == 0:
                    abort(404)
                conn.commit()
            return jsonify({"ok": True})

        @app.post("/api/outreach/send")
        def api_outreach_send():
            data = request.get_json(silent=True) or {}
            pin = data.get("pin") or ""
            to = data.get("to") or ""
            to_list = data.get("to_list") or []
            # Backwards-compat: if only `to` was supplied, treat it as a 1-item list.
            if to and not to_list:
                to_list = [to]
            subject = data.get("subject")
            body = data.get("body")
            touch_number = data.get("touch_number", 1)
            if not isinstance(touch_number, int) or touch_number < 1:
                abort(400, "touch_number must be a positive integer")
            if not pin.isdigit() or len(pin) != 14:
                abort(400, "invalid pin")
            if not to_list:
                abort(400, "to or to_list is required")
            for addr in to_list:
                if not EMAIL_RE.match(addr):
                    abort(400, f"invalid recipient email: {addr}")
            if not subject or body is None:
                abort(400, "subject and body are required")

            subject = outreach_module.sanitize_subject(subject)
            sender = app.config.get("GMAIL_SENDER_ADDRESS") or ""
            if not sender:
                abort(503, "GMAIL_SENDER_ADDRESS is not set")

            with closing(_conn()) as conn:
                _parcel_or_404(conn, pin)
                # Every requested address must already be an alive contact
                # for this pin — guards against fat-fingered manual sends
                # bypassing the dead/wrong_person tombstones.
                alive = {r["email"] for r in conn.execute(
                    "SELECT email FROM contacts WHERE pin=? AND dead=0 AND wrong_person=0 "
                    "AND email IS NOT NULL", (pin,)
                )}
                bad = [a for a in to_list if a not in alive]
                if bad:
                    abort(400, f"addresses are not alive contacts: {', '.join(bad)}")
                # Validate the touch_number is next-due for this parcel.
                outreach_rows = [
                    dict(r) for r in conn.execute(
                        "SELECT * FROM outreach WHERE pin = ? "
                        "ORDER BY touch_number",
                        (pin,),
                    )
                ]
                try:
                    outreach_module.validate_next_due_touch(
                        outreach_rows=outreach_rows,
                        touch_number=touch_number,
                    )
                except ValueError as e:
                    abort(400, str(e))

                # Per-recipient fan-out: each address in to_list gets a
                # separately-addressed Gmail send (To: = recipient, no BCC)
                # AND its own outreach row tied to that recipient's
                # contact_id. Best-effort: a per-recipient HttpError doesn't
                # abort the batch — successful recipients still get rows.
                # Global auth errors (RefreshError / GmailNotConnectedError)
                # DO abort, since every subsequent attempt would fail the
                # same way and spamming N identical errors is just noise.
                results: list[dict] = []
                for addr in to_list:
                    cid = outreach_module.upsert_contact(
                        conn, pin=pin, email=addr, source="manual",
                    )
                    try:
                        sent = gmail_client.send_email(
                            token_path=Path(app.config["GMAIL_TOKEN_PATH"]),
                            sender=sender,
                            to=addr,                 # direct addressed send
                            subject=subject, body=body,
                        )
                    except gmail_client.GmailNotConnectedError as e:
                        abort(503, f"Gmail not connected: {e}")
                    except RefreshError as e:
                        # load_credentials already translates RefreshError to
                        # GmailNotConnectedError. Defense-in-depth for any
                        # future path where RefreshError escapes.
                        abort(503, f"Gmail not connected: refresh token rejected ({e}). "
                              "Re-consent at /api/oauth/start.")
                    except HttpError as e:
                        # Per-recipient transient (quota, forbidden, 5xx) —
                        # record + continue. Surfaced in the response so the
                        # operator sees which addresses didn't go through.
                        results.append({
                            "to": addr,
                            "status": "failed",
                            "error": f"Gmail API error: {e}",
                        })
                        continue

                    try:
                        oid = outreach_module.create_outreach_record(
                            conn, pin=pin, contact_id=cid,
                            channel="email", subject=subject, body=body,
                            sent_date=_now_iso(),
                            touch_number=touch_number,
                        )
                    except sqlite3.IntegrityError:
                        # Same (pin, touch_number, contact_id) already exists
                        # — operator clicked send twice for the same person.
                        # Email already went out though, so this is a soft
                        # failure: report it, keep going.
                        results.append({
                            "to": addr,
                            "status": "failed",
                            "error": "touch already sent to this contact",
                        })
                        continue
                    conn.execute(
                        "UPDATE outreach SET gmail_message_id = ? WHERE outreach_id = ?",
                        (sent.get("id", ""), oid),
                    )
                    results.append({
                        "to": addr,
                        "status": "sent",
                        "outreach_id": oid,
                        "gmail_message_id": sent.get("id", ""),
                        "gmail_thread_id": sent.get("threadId", ""),
                    })

                # Auto-transition scored → outreach if any send succeeded.
                if any(r["status"] == "sent" for r in results):
                    conn.execute(
                        "UPDATE parcels SET stage = 'outreach' "
                        "WHERE pin = ? AND (stage IS NULL OR stage = 'scored')",
                        (pin,),
                    )
                conn.commit()

            sent_count = sum(1 for r in results if r["status"] == "sent")
            failed_count = len(results) - sent_count
            # HTTP status: 200 all-sent, 207 partial, 503 all-failed.
            status_code = (
                200 if failed_count == 0
                else 503 if sent_count == 0
                else 207
            )
            return jsonify({
                "results": results,
                "sent": sent_count,
                "failed": failed_count,
            }), status_code

        @app.post("/api/outreach/<int:outreach_id>/mark-replied")
        def api_outreach_mark_replied(outreach_id: int):
            data = request.get_json(silent=True) or {}
            response_type = data.get("response_type", "responded")
            if response_type not in ALLOWED_RESPONSE_TYPES:
                abort(400, "invalid response_type")
            with closing(_conn()) as conn:
                row = conn.execute(
                    "SELECT outreach_id, pin FROM outreach WHERE outreach_id = ?",
                    (outreach_id,),
                ).fetchone()
                if row is None:
                    abort(404)
                outreach_module.mark_replied(
                    conn, outreach_id,
                    response_date=_now_iso(),
                    response_type=response_type,
                )
                # If this was the first reply, also bump parcel stage to "responded".
                conn.execute(
                    "UPDATE parcels SET stage = 'responded' "
                    "WHERE pin = ? AND stage = 'outreach'",
                    (row["pin"],),
                )
                conn.commit()
            return jsonify({"ok": True})

        @app.post("/api/parcels/<pin>/stage")
        def api_parcel_set_stage(pin: str):
            data = request.get_json(silent=True) or {}
            stage = data.get("stage")
            if stage not in ALLOWED_STAGES:
                abort(400, "invalid stage")
            with closing(_conn()) as conn:
                _parcel_or_404(conn, pin)
                conn.execute(
                    "UPDATE parcels SET stage = ? WHERE pin = ?", (stage, pin)
                )
                conn.commit()
            return jsonify({"ok": True, "stage": stage})

        # ----- OAuth -----

        @app.get("/api/oauth/start")
        def api_oauth_start():
            client_path = Path(app.config["GMAIL_CLIENT_SECRETS_PATH"])
            if not client_path.exists():
                abort(503,
                      f"Gmail client secrets not found at {client_path}. "
                      "Download from Google Cloud Console and save the file there.")
            redirect_uri = url_for("api_oauth_callback", _external=True)
            url, _state = gmail_client.build_authorization_url(
                client_secrets_path=client_path, redirect_uri=redirect_uri,
            )
            return redirect(url)

        @app.get("/api/oauth/callback")
        def api_oauth_callback():
            client_path = Path(app.config["GMAIL_CLIENT_SECRETS_PATH"])
            if not client_path.exists():
                abort(503, "Gmail client secrets not found")
            redirect_uri = url_for("api_oauth_callback", _external=True)
            try:
                gmail_client.exchange_code_for_token(
                    client_secrets_path=client_path,
                    redirect_uri=redirect_uri,
                    authorization_response_url=request.url,
                    token_path=Path(app.config["GMAIL_TOKEN_PATH"]),
                )
            except Exception as e:
                return f"OAuth callback failed: {e}", 500
            # Send the user back to the main UI.
            return redirect(url_for("index"))

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
      ?lot_size_sf={"between":[3500,12000]}     -> {"lot_size_sf": {"between": [3500, 12000]}}
      ?zone_class={"prefix_in":["RT-","RM-"]}   -> {"zone_class": {"prefix_in": ["RT-", "RM-"]}}
      ?lot_width_ft={"not_null":true}           -> {"lot_width_ft": {"not_null": True}}

    The JSON-object encoding is produced by filterStateToQuery() in filters.js
    for complex operators (between, not_null, prefix_in, in) that don't have
    a dedicated dot-suffix scheme.
    """
    import json as _json

    # Operators that are safe to accept as JSON-decoded dicts.
    ALLOWED_DICT_OPERATORS = {"between", "min", "max", "not_null", "prefix_in", "in"}

    filters: dict[str, Any] = {}
    seen_keys: set[str] = set()
    for key in args.keys():
        if key in seen_keys:
            continue
        seen_keys.add(key)
        if key in {"limit", "offset", "stage", "sort", "dir", "include_condo_units",
                   "top_n_only", "profile", "categories"}:
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
        elif value.startswith("{"):
            # Attempt to decode as a JSON operator dict
            try:
                decoded = _json.loads(value)
            except (_json.JSONDecodeError, ValueError):
                filters[key] = value
                continue
            if not isinstance(decoded, dict):
                filters[key] = value
                continue
            # Reject any key not in the allowed operator set to prevent
            # unexpected dict shapes from reaching _build_where.
            if not all(k in ALLOWED_DICT_OPERATORS for k in decoded):
                continue
            filters[key] = decoded
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
