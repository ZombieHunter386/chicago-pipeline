"""Source 2D — Chicago Building Violations."""
from __future__ import annotations
from datetime import datetime, date, UTC
from pathlib import Path
from math import radians, sin, cos, sqrt, atan2
from collections import defaultdict
from pipeline.config import GeographyConfig
from pipeline.db import upsert_rows, get_connection
from pipeline.geography import filter_by_polygon, bbox_where_clause
from pipeline.socrata import SocrataClient


DATASET_ID = "22u3-xenr"
TABLE = "raw_cdp_violations"
SOURCE_NAME = "cdp_violations"
TODAY = date.today()
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
        parcels = conn.execute(
            "SELECT pin, lat, lng FROM parcels WHERE lat IS NOT NULL AND lng IS NOT NULL"
        ).fetchall()
    finally:
        conn.close()
    if not parcels:
        return n

    open_count: dict[str, int] = defaultdict(int)
    oldest_open: dict[str, str] = {}
    for r in raw_rows:
        # Real dataset uses "OPEN", "OPEN - HEARING", "OPEN - REFERRED TO LAW",
        # etc. Match any status that starts with "OPEN".
        if not (r.get("violation_status") or "").upper().startswith("OPEN"):
            continue
        if not r["latitude"] or not r["longitude"]:
            continue
        best_pin, best_d = None, MATCH_RADIUS_FT
        for p in parcels:
            d = _haversine_ft(r["latitude"], r["longitude"], p["lat"], p["lng"])
            if d <= best_d:
                best_d, best_pin = d, p["pin"]
        if best_pin is None:
            continue
        open_count[best_pin] += 1
        vd = r["violation_date"]
        if vd and (best_pin not in oldest_open or vd < oldest_open[best_pin]):
            oldest_open[best_pin] = vd

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
