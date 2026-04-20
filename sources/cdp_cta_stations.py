"""Source 2F — Chicago CTA L Stations."""
from __future__ import annotations
from datetime import datetime, UTC
from pathlib import Path
from math import radians, sin, cos, sqrt, atan2
from pipeline.config import GeographyConfig
from pipeline.db import upsert_rows, get_connection
from pipeline.socrata import SocrataClient


DATASET_ID = "3tzw-cg4m"
TABLE = "raw_cdp_cta_stations"
SOURCE_NAME = "cdp_cta_stations"


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


def _extract_latlng(r: dict) -> tuple[float | None, float | None]:
    lat = _f(r.get("latitude"))
    lng = _f(r.get("longitude"))
    if lat is not None and lng is not None:
        return lat, lng
    loc = r.get("location") or r.get("the_geom")
    if isinstance(loc, dict) and loc.get("type") == "Point":
        coords = loc.get("coordinates") or []
        if len(coords) == 2:
            return _f(coords[1]), _f(coords[0])
    return None, None


def fetch(geo: GeographyConfig, db_path: Path, client: SocrataClient) -> int:
    fetched_at = datetime.now(UTC).isoformat(timespec="seconds")

    raw_rows = []
    stations = []
    for r in client.fetch(DATASET_ID):
        lat, lng = _extract_latlng(r)
        sid = r.get("station_id") or r.get("stop_id")
        if not sid:
            continue
        raw_rows.append({
            "station_id": str(sid),
            "longname": r.get("longname") or r.get("station_name"),
            "lines": r.get("lines"),
            "latitude": lat, "longitude": lng,
            "fetched_at": fetched_at,
        })
        if lat is not None and lng is not None:
            stations.append({
                "id": str(sid),
                "name": r.get("longname") or r.get("station_name"),
                "lat": lat, "lng": lng,
            })

    n = upsert_rows(db_path, TABLE, raw_rows, key_columns=["station_id"])

    if not stations:
        return n

    conn = get_connection(db_path)
    try:
        parcels = conn.execute(
            "SELECT pin, lat, lng FROM parcels WHERE lat IS NOT NULL AND lng IS NOT NULL"
        ).fetchall()
    finally:
        conn.close()
    if not parcels:
        return n

    conn = get_connection(db_path)
    try:
        for p in parcels:
            best_name, best_d = None, float("inf")
            for s in stations:
                d = _haversine_ft(p["lat"], p["lng"], s["lat"], s["lng"])
                if d < best_d:
                    best_d, best_name = d, s["name"]
            conn.execute("""
                UPDATE parcels SET
                    cta_nearest_station = :name,
                    cta_distance_ft = :d,
                    last_updated_date = :t
                WHERE pin = :pin
            """, {"name": best_name, "d": round(best_d, 1),
                  "t": fetched_at, "pin": p["pin"]})
        conn.commit()
    finally:
        conn.close()
    return n
