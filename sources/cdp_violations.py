"""Source 2D — Chicago Building Violations."""
from __future__ import annotations
from datetime import datetime, date, UTC
from pathlib import Path
from collections import defaultdict
from pipeline.config import GeographyConfig
from pipeline.db import upsert_rows, get_connection
from pipeline.geography import filter_by_polygon, bbox_where_clause
from pipeline.socrata import SocrataClient
from pipeline.spatial import match_records_to_parcels


DATASET_ID = "22u3-xenr"
TABLE = "raw_cdp_violations"
SOURCE_NAME = "cdp_violations"
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
            "violation_id": r.get("id") or r.get("violation_id"),
            "violation_date": (r.get("violation_date") or "")[:10] or None,
            "violation_code": r.get("violation_code"),
            "violation_status": r.get("violation_status"),
            "violation_description": r.get("violation_description"),
            "inspection_category": r.get("inspection_category"),
            "department_bureau": r.get("department_bureau"),
            "address": r.get("address"),
            "street_number": r.get("street_number"),
            "street_direction": r.get("street_direction"),
            "street_name": r.get("street_name"),
            "property_group": r.get("property_group"),
            "latitude": _f(r.get("latitude")),
            "longitude": _f(r.get("longitude")),
            "fetched_at": fetched_at,
        })
    raw_rows = filter_by_polygon(raw_rows, geo, lat_field="latitude", lng_field="longitude")
    raw_rows = [r for r in raw_rows if r["violation_id"]]
    n = upsert_rows(db_path, TABLE, raw_rows, key_columns=["violation_id"])

    conn = get_connection(db_path)
    try:
        parcels = [dict(p) for p in conn.execute(
            "SELECT pin, lat, lng FROM parcels WHERE lat IS NOT NULL AND lng IS NOT NULL"
        ).fetchall()]
    finally:
        conn.close()
    if not parcels:
        return n

    # Real dataset uses "OPEN", "OPEN - HEARING", "OPEN - REFERRED TO LAW",
    # etc. Match any status that starts with "OPEN".
    open_indices = {
        i for i, r in enumerate(raw_rows)
        if (r.get("violation_status") or "").upper().startswith("OPEN")
    }
    matches = match_records_to_parcels(raw_rows, parcels, MATCH_RADIUS_FT)
    open_count: dict[str, int] = defaultdict(int)
    oldest_open: dict[str, str] = {}
    for idx, pin in matches.items():
        if idx not in open_indices:
            continue
        r = raw_rows[idx]
        open_count[pin] += 1
        vd = r["violation_date"]
        if vd and (pin not in oldest_open or vd < oldest_open[pin]):
            oldest_open[pin] = vd

    conn = get_connection(db_path)
    try:
        for pin, cnt in open_count.items():
            age = None
            if pin in oldest_open:
                d = datetime.strptime(oldest_open[pin], "%Y-%m-%d").date()
                age = (TODAY - d).days
            conn.execute("""
                UPDATE parcels SET
                    open_violations_count = :c,
                    oldest_violation_age_days = :age,
                    last_updated_date = :t
                WHERE pin = :pin
            """, {"c": cnt, "age": age, "t": fetched_at, "pin": pin})
        conn.commit()
    finally:
        conn.close()
    return n
