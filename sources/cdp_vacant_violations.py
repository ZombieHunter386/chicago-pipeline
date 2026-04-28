"""Source 2H — Chicago Vacant and Abandoned Buildings Violations (kc9i-wq85).

Administrative-hearing violations issued under Chicago Municipal Code 13-12
(secure/maintain vacant buildings, watchman required, etc.). Different from
the 311 vacancy reports (Source 2E) — this is actual enforcement, not
citizen complaints. Volume peaked in the post-foreclosure-crisis years
(2011–2016) and has tailed off since, but a few recent rows still appear
through 2025-08. Records carry per-violation fines and a current_amount_due
that's non-zero when enforcement is still active.
"""
from __future__ import annotations
from collections import defaultdict
from datetime import datetime, UTC
from pathlib import Path

from pipeline.config import GeographyConfig
from pipeline.db import upsert_rows, get_connection
from pipeline.geography import filter_by_polygon, bbox_where_clause
from pipeline.socrata import SocrataClient
from pipeline.spatial import (
    DEFAULT_GEO_RADIUS_FT,
    match_records_to_parcels_with_address,
)


DATASET_ID = "kc9i-wq85"
TABLE = "raw_cdp_vacant_violations"
SOURCE_NAME = "cdp_vacant_violations"


def _f(v):
    if v in (None, ""): return None
    try: return float(v)
    except (TypeError, ValueError): return None


def _record_address(r: dict) -> str | None:
    return r.get("property_address")


def fetch(geo: GeographyConfig, db_path: Path, client: SocrataClient) -> int:
    fetched_at = datetime.now(UTC).isoformat(timespec="seconds")
    where = bbox_where_clause(geo, lat_field="latitude", lng_field="longitude")

    raw_rows = []
    for r in client.fetch(DATASET_ID, where=where):
        raw_rows.append({
            "docket_number": r.get("docket_number"),
            "violation_number": r.get("violation_number"),
            "issued_date": (r.get("issued_date") or "")[:10] or None,
            "issuing_department": r.get("issuing_department"),
            "last_hearing_date": (r.get("last_hearing_date") or "")[:10] or None,
            "property_address": r.get("property_address"),
            "violation_type": r.get("violation_type"),
            # Source field has a trailing underscore.
            "entity_or_person": r.get("entity_or_person_s_") or r.get("entity_or_person"),
            "disposition_description": r.get("disposition_description"),
            "total_fines": _f(r.get("total_fines")),
            "total_administrative_costs": _f(r.get("total_administrative_costs")),
            "interest_amount": _f(r.get("interest_amount")),
            "collection_costs_or_attorney_fees": _f(r.get("collection_costs_or_attorney_fees")),
            "court_cost": _f(r.get("court_cost")),
            "original_total_amount_due": _f(r.get("original_total_amount_due")),
            "total_paid": _f(r.get("total_paid")),
            "current_amount_due": _f(r.get("current_amount_due")),
            "latitude": _f(r.get("latitude")),
            "longitude": _f(r.get("longitude")),
            "fetched_at": fetched_at,
        })
    raw_rows = filter_by_polygon(raw_rows, geo, lat_field="latitude", lng_field="longitude")
    raw_rows = [r for r in raw_rows if r["docket_number"]]
    n = upsert_rows(db_path, TABLE, raw_rows, key_columns=["docket_number"])

    conn = get_connection(db_path)
    try:
        parcels = [dict(p) for p in conn.execute(
            "SELECT pin, address, lat, lng FROM parcels"
        ).fetchall()]
    finally:
        conn.close()
    if not parcels:
        return n

    matches, _fuzzy = match_records_to_parcels_with_address(
        raw_rows, parcels,
        get_record_address=_record_address,
        geo_radius_ft=DEFAULT_GEO_RADIUS_FT,
    )

    counts: dict[str, int] = defaultdict(int)
    amount_due: dict[str, float] = defaultdict(float)
    most_recent: dict[str, str] = {}
    for idx, (pin, _method) in matches.items():
        r = raw_rows[idx]
        counts[pin] += 1
        if r["current_amount_due"] is not None:
            amount_due[pin] += r["current_amount_due"]
        idate = r["issued_date"]
        if idate and (pin not in most_recent or idate > most_recent[pin]):
            most_recent[pin] = idate

    conn = get_connection(db_path)
    try:
        # Reset previous values so a stale re-run reflects only the current
        # dataset — same pattern as scofflaw.
        conn.execute(
            "UPDATE parcels SET vacant_violations_count = NULL, "
            "vacant_violations_amount_due = NULL, "
            "most_recent_vacant_violation_date = NULL "
            "WHERE vacant_violations_count IS NOT NULL"
        )
        for pin, c in counts.items():
            conn.execute("""
                UPDATE parcels SET
                    vacant_violations_count = :c,
                    vacant_violations_amount_due = :a,
                    most_recent_vacant_violation_date = :d,
                    last_updated_date = :now
                WHERE pin = :pin
            """, {
                "c": c,
                "a": round(amount_due.get(pin, 0.0), 2) if pin in amount_due else None,
                "d": most_recent.get(pin),
                "now": fetched_at,
                "pin": pin,
            })
        conn.commit()
    finally:
        conn.close()
    return n
