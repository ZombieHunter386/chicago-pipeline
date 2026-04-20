"""Source 2E — Chicago Vacant and Abandoned Buildings."""
from __future__ import annotations
from datetime import datetime, UTC
from pathlib import Path
from math import radians, sin, cos, sqrt, atan2
from pipeline.config import GeographyConfig
from pipeline.db import upsert_rows, get_connection
from pipeline.geography import filter_by_polygon, bbox_where_clause
from pipeline.socrata import SocrataClient


DATASET_ID = "7nii-7srd"
TABLE = "raw_cdp_vacant"
SOURCE_NAME = "cdp_vacant"
MATCH_RADIUS_FT = 50.0


def _f(v):
    if v in (None, ""): return None
    try: return float(v)
    except (TypeError, ValueError): return None


def _haversine_ft(lat1, lng1, lat2, lng2):
    R = 6_371_000
    a1, a2 = radians(lat1), radians(lat2)
    da = radians(lat2 - lat1); dl = radians(lng2 - lng1)
    a = sin(da/2)**2 + cos(a1) * cos(a2) * sin(dl/2)**2
    c = 2 * atan2(sqrt(a), sqrt(1-a))
    return R * c * 3.28084


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
    except Exception:
        # Sparse dataset — failure is acceptable
        return 0

    raw_rows = filter_by_polygon(raw_rows, geo, lat_field="latitude", lng_field="longitude")
    raw_rows = [r for r in raw_rows if r["service_request_number"]]
    n = upsert_rows(db_path, TABLE, raw_rows, key_columns=["service_request_number"])

    conn = get_connection(db_path)
    try:
        parcels = conn.execute(
            "SELECT pin, lat, lng FROM parcels WHERE lat IS NOT NULL AND lng IS NOT NULL"
        ).fetchall()
    finally:
        conn.close()
    if not parcels:
        return n

    flagged: set[str] = set()
    for r in raw_rows:
        if not r["latitude"] or not r["longitude"]:
            continue
        best_pin, best_d = None, MATCH_RADIUS_FT
        for p in parcels:
            d = _haversine_ft(r["latitude"], r["longitude"], p["lat"], p["lng"])
            if d <= best_d:
                best_d, best_pin = d, p["pin"]
        if best_pin:
            flagged.add(best_pin)

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
