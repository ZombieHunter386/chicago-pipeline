# sources/assessor_parcels.py
"""Source 1A — Cook County Assessor Parcel Universe."""
from __future__ import annotations
from datetime import datetime, UTC
from pathlib import Path
from pipeline.config import GeographyConfig
from pipeline.db import upsert_rows, get_connection
from pipeline.geography import filter_by_polygon, bbox_where_clause
from pipeline.socrata import SocrataClient


DATASET_ID = "nj4t-kc8j"
TABLE = "raw_assessor_parcels"
SOURCE_NAME = "assessor_parcels"


def _to_float(v):
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def fetch(geo: GeographyConfig, db_path: Path, client: SocrataClient) -> int:
    where = bbox_where_clause(geo, lat_field="lat", lng_field="lon")
    # Pull current year only — historical years not needed for this source
    where = f"({where})"
    fetched_at = datetime.now(UTC).isoformat(timespec="seconds")

    raw_rows = []
    for r in client.fetch(DATASET_ID, where=where):
        raw_rows.append({
            "pin": r.get("pin"),
            "year": r.get("year"),
            "pin10": r.get("pin10"),
            "class": r.get("class"),
            "lat": _to_float(r.get("lat")),
            "lon": _to_float(r.get("lon")),
            "ward_num": r.get("ward_num"),
            "zip_code": r.get("zip_code"),
            "tax_tif_district_num": r.get("tax_tif_district_num"),
            "tax_tif_district_name": r.get("tax_tif_district_name"),
            "township_code": r.get("township_code"),
            "nbhd_code": r.get("nbhd_code"),
            "fetched_at": fetched_at,
        })

    # Precise polygon filter (bbox was a coarse SoQL prefilter)
    raw_rows = filter_by_polygon(raw_rows, geo, lat_field="lat", lng_field="lon")

    n = upsert_rows(db_path, TABLE, raw_rows, key_columns=["pin", "year"])

    # Also upsert into the parcels table with identity columns
    parcel_rows = [{
        "pin": r["pin"],
        "pin10": r["pin10"],
        "lat": r["lat"],
        "lng": r["lon"],
        "ward_num": r["ward_num"],
        "zip_code": r["zip_code"],
        "property_class": r["class"],
        "tif_district": r["tax_tif_district_name"],
        "first_seen_date": fetched_at,
        "last_fetched_date": fetched_at,
        "last_updated_date": fetched_at,
        "stage": "scored",
    } for r in raw_rows]
    _upsert_parcels(db_path, parcel_rows)
    return n


def _upsert_parcels(db_path: Path, rows: list[dict]) -> None:
    """
    Special-case upsert into parcels: never overwrite first_seen_date on
    existing rows.
    """
    if not rows:
        return
    conn = get_connection(db_path)
    try:
        for r in rows:
            conn.execute("""
                INSERT INTO parcels (pin, pin10, lat, lng, ward_num, zip_code,
                                     property_class, tif_district,
                                     first_seen_date, last_fetched_date,
                                     last_updated_date, stage)
                VALUES (:pin, :pin10, :lat, :lng, :ward_num, :zip_code,
                        :property_class, :tif_district,
                        :first_seen_date, :last_fetched_date,
                        :last_updated_date, :stage)
                ON CONFLICT(pin) DO UPDATE SET
                    pin10=excluded.pin10,
                    lat=excluded.lat,
                    lng=excluded.lng,
                    ward_num=excluded.ward_num,
                    zip_code=excluded.zip_code,
                    property_class=excluded.property_class,
                    tif_district=excluded.tif_district,
                    last_fetched_date=excluded.last_fetched_date,
                    last_updated_date=excluded.last_updated_date
            """, r)
        conn.commit()
    finally:
        conn.close()
