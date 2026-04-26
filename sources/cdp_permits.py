"""Source 2C — Chicago Building Permits."""
from __future__ import annotations
from datetime import datetime, date, UTC
from pathlib import Path
from pipeline.config import GeographyConfig
from pipeline.db import upsert_rows, get_connection
from pipeline.geography import filter_by_polygon, bbox_where_clause
from pipeline.socrata import SocrataClient
from pipeline.spatial import match_records_to_parcels


DATASET_ID = "ydr8-5enu"
TABLE = "raw_cdp_permits"
SOURCE_NAME = "cdp_permits"
TODAY = date.today()
MATCH_RADIUS_FT = 50.0


def _f(v):
    if v in (None, ""): return None
    try: return float(v)
    except (TypeError, ValueError): return None


def fetch(geo: GeographyConfig, db_path: Path, client: SocrataClient) -> int:
    fetched_at = datetime.now(UTC).isoformat(timespec="seconds")
    where = bbox_where_clause(geo, lat_field="latitude", lng_field="longitude")

    raw_rows = []
    for r in client.fetch(DATASET_ID, where=where):
        raw_rows.append({
            "permit_number": r.get("permit_") or r.get("permit_number"),
            "permit_type": r.get("permit_type"),
            "issue_date": (r.get("issue_date") or "")[:10] or None,
            "street_number": r.get("street_number"),
            "street_direction": r.get("street_direction"),
            "street_name": r.get("street_name"),
            "work_description": r.get("work_description"),
            "reported_cost": _f(r.get("reported_cost")),
            "community_area": r.get("community_area"),
            "ward": r.get("ward"),
            "latitude": _f(r.get("latitude")),
            "longitude": _f(r.get("longitude")),
            "fetched_at": fetched_at,
        })
    raw_rows = filter_by_polygon(raw_rows, geo, lat_field="latitude", lng_field="longitude")
    raw_rows = [r for r in raw_rows if r["permit_number"]]
    n = upsert_rows(db_path, TABLE, raw_rows, key_columns=["permit_number"])

    # Match each permit to nearest parcel within MATCH_RADIUS_FT
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
    latest: dict[str, str] = {}
    for idx, pin in matches.items():
        r = raw_rows[idx]
        if not r["issue_date"]:
            continue
        if pin not in latest or r["issue_date"] > latest[pin]:
            latest[pin] = r["issue_date"]

    conn = get_connection(db_path)
    try:
        for pin, dt in latest.items():
            d = datetime.strptime(dt, "%Y-%m-%d").date()
            yrs = round((TODAY - d).days / 365.25, 2)
            conn.execute(
                "UPDATE parcels SET years_since_last_permit=:y, last_updated_date=:t WHERE pin=:p",
                {"y": yrs, "t": fetched_at, "p": pin},
            )
        conn.commit()
    finally:
        conn.close()
    return n
