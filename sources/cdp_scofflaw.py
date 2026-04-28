"""Source 2G — Chicago Building Code Scofflaw List (crg5-4zyp).

The Scofflaw List is a small (~600-row) dataset published monthly listing
buildings whose owners are being sued by the City in Cook County Circuit
Court for chronic code violations. Every record carries a `building_list_date`
indicating which monthly publication it appeared on, so the same building
across multiple months produces multiple records — `scofflaw_appearances_count`
captures how chronic the listing is.

Coverage is concentrated in distressed South/West Side neighborhoods; LP/LV
hits a small handful of parcels but each one is high-grade signal.
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


DATASET_ID = "crg5-4zyp"
TABLE = "raw_cdp_scofflaw"
SOURCE_NAME = "cdp_scofflaw"


def _f(v):
    if v in (None, ""): return None
    try: return float(v)
    except (TypeError, ValueError): return None


def _record_address(r: dict) -> str | None:
    return r.get("address")


def fetch(geo: GeographyConfig, db_path: Path, client: SocrataClient) -> int:
    fetched_at = datetime.now(UTC).isoformat(timespec="seconds")
    where = bbox_where_clause(geo, lat_field="latitude", lng_field="longitude")

    raw_rows = []
    for r in client.fetch(DATASET_ID, where=where):
        raw_rows.append({
            "record_id": r.get("record_id"),
            "address": r.get("address"),
            "secondary_address": r.get("secondary_address"),
            "tertiary_address": r.get("tertiary_address"),
            "defendant_owner": r.get("defendant_owner"),
            "circuit_court_case_number": r.get("circuit_court_case_number"),
            "building_list_date": (r.get("building_list_date") or "")[:10] or None,
            "owner_list_date": (r.get("owner_list_date") or "")[:10] or None,
            "community_area": r.get("community_area"),
            "ward": r.get("ward"),
            "latitude": _f(r.get("latitude")),
            "longitude": _f(r.get("longitude")),
            "fetched_at": fetched_at,
        })
    raw_rows = filter_by_polygon(raw_rows, geo, lat_field="latitude", lng_field="longitude")
    raw_rows = [r for r in raw_rows if r["record_id"]]
    n = upsert_rows(db_path, TABLE, raw_rows, key_columns=["record_id"])

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

    # Per-PIN aggregation. A parcel's "appearances count" is the number of
    # distinct (building_list_date, record_id) pairs we saw — record_id
    # already encodes case-number + list-date uniquely.
    appearances: dict[str, int] = defaultdict(int)
    most_recent: dict[str, str] = {}
    for idx, (pin, _method) in matches.items():
        r = raw_rows[idx]
        appearances[pin] += 1
        ld = r["building_list_date"]
        if ld and (pin not in most_recent or ld > most_recent[pin]):
            most_recent[pin] = ld

    conn = get_connection(db_path)
    try:
        # Reset previous flags so re-runs reflect the current list (an owner
        # that paid up and is no longer being sued should drop off).
        conn.execute(
            "UPDATE parcels SET is_scofflaw = 0, scofflaw_appearances_count = 0, "
            "most_recent_scofflaw_list_date = NULL WHERE is_scofflaw = 1"
        )
        for pin, count in appearances.items():
            conn.execute("""
                UPDATE parcels SET
                    is_scofflaw = 1,
                    scofflaw_appearances_count = :c,
                    most_recent_scofflaw_list_date = :d,
                    last_updated_date = :now
                WHERE pin = :pin
            """, {"c": count, "d": most_recent.get(pin), "now": fetched_at, "pin": pin})
        conn.commit()
    finally:
        conn.close()
    return n
