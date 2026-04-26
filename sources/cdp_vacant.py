"""Source 2E — Chicago Vacant and Abandoned Buildings."""
from __future__ import annotations
import logging
from datetime import datetime, UTC
from pathlib import Path
from pipeline.config import GeographyConfig
from pipeline.db import upsert_rows, get_connection
from pipeline.geography import filter_by_polygon, bbox_where_clause
from pipeline.socrata import SocrataClient, SocrataError
from pipeline.spatial import match_records_to_parcels

log = logging.getLogger(__name__)


DATASET_ID = "7nii-7srd"
TABLE = "raw_cdp_vacant"
SOURCE_NAME = "cdp_vacant"
MATCH_RADIUS_FT = 50.0


def _f(v):
    if v in (None, ""): return None
    try: return float(v)
    except (TypeError, ValueError): return None


def fetch(geo: GeographyConfig, db_path: Path, client: SocrataClient) -> int:
    fetched_at = datetime.now(UTC).isoformat(timespec="seconds")
    where = bbox_where_clause(geo, lat_field="latitude", lng_field="longitude")

    raw_rows = []
    try:
        for r in client.fetch(DATASET_ID, where=where):
            raw_rows.append({
                "service_request_number": r.get("service_request_number"),
                "date_service_request_was_received": (r.get("date_service_request_was_received") or "")[:10] or None,
                "location_of_building_on_the_lot": r.get("location_of_building_on_the_lot"),
                "is_the_building_dangerous_or_hazardous": r.get("is_the_building_dangerous_or_hazardous"),
                "address_street_number": r.get("address_street_number"),
                "address_street_direction": r.get("address_street_direction"),
                "address_street_name": r.get("address_street_name"),
                "latitude": _f(r.get("latitude")),
                "longitude": _f(r.get("longitude")),
                "fetched_at": fetched_at,
            })
    except SocrataError as e:
        # Sparse dataset — a missing/empty response is acceptable, but log
        # the specific Socrata error rather than silently hiding all failures.
        log.warning("cdp_vacant fetch failed: %s", e)
        return 0

    raw_rows = filter_by_polygon(raw_rows, geo, lat_field="latitude", lng_field="longitude")
    raw_rows = [r for r in raw_rows if r["service_request_number"]]
    n = upsert_rows(db_path, TABLE, raw_rows, key_columns=["service_request_number"])

    conn = get_connection(db_path)
    try:
        parcels = [dict(p) for p in conn.execute(
            "SELECT pin, lat, lng FROM parcels WHERE lat IS NOT NULL AND lng IS NOT NULL"
        ).fetchall()]
    finally:
        conn.close()
    if not parcels:
        return n

    matches = match_records_to_parcels(raw_rows, parcels, MATCH_RADIUS_FT)
    flagged: set[str] = set(matches.values())

    conn = get_connection(db_path)
    try:
        for pin in flagged:
            conn.execute(
                "UPDATE parcels SET has_vacancy_report=1, last_updated_date=:t WHERE pin=:pin",
                {"t": fetched_at, "pin": pin},
            )
        conn.commit()
    finally:
        conn.close()
    return n
