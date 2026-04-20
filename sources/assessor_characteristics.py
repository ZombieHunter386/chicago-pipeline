"""Source 1C — Cook County Assessor Improvement Characteristics."""
from __future__ import annotations
from datetime import datetime, UTC
from pathlib import Path
from pipeline.config import GeographyConfig
from pipeline.db import upsert_rows, get_connection
from pipeline.socrata import SocrataClient


DATASET_ID = "x54s-btds"
TABLE = "raw_assessor_characteristics"
SOURCE_NAME = "assessor_characteristics"


def _f(v):
    if v in (None, ""): return None
    try: return float(v)
    except (TypeError, ValueError): return None

def _i(v):
    if v in (None, ""): return None
    try: return int(float(v))
    except (TypeError, ValueError): return None

def _b(v):
    if v in (None, ""): return None
    return 1 if str(v).lower() in ("true", "t", "y", "yes", "1") else 0


def fetch(geo: GeographyConfig, db_path: Path, client: SocrataClient) -> int:
    fetched_at = datetime.now(UTC).isoformat(timespec="seconds")
    conn = get_connection(db_path)
    try:
        known_pins = {row[0] for row in conn.execute("SELECT pin FROM parcels")}
    finally:
        conn.close()
    if not known_pins:
        return 0

    # Pull most recent year only for each PIN by ordering desc and de-duping
    raw_rows: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for r in client.fetch(DATASET_ID, order="year DESC"):
        pin = r.get("pin")
        year = r.get("year")
        if pin not in known_pins:
            continue
        if (pin, year) in seen:
            continue
        seen.add((pin, year))
        raw_rows.append({
            "pin": pin, "year": year, "class": r.get("class"),
            "char_land_sf": _f(r.get("char_land_sf")),
            "char_bldg_sf": _f(r.get("char_bldg_sf")),
            "char_yrblt": r.get("char_yrblt"),
            "char_cnst_qlty": r.get("char_cnst_qlty"),
            "char_repair_cnd": r.get("char_repair_cnd"),
            "cdu": r.get("cdu"),
            "char_beds": r.get("char_beds"),
            "char_rooms": r.get("char_rooms"),
            "char_fbath": r.get("char_fbath"),
            "char_hbath": r.get("char_hbath"),
            "char_type_resd": r.get("char_type_resd"),
            "char_ext_wall": r.get("char_ext_wall"),
            "char_heat": r.get("char_heat"),
            "char_bsmt": r.get("char_bsmt"),
            "char_bsmt_fin": r.get("char_bsmt_fin"),
            "char_gar1_att": r.get("char_gar1_att"),
            "char_gar1_area": r.get("char_gar1_area"),
            "char_use": r.get("char_use"),
            "char_site": r.get("char_site"),
            "char_air": r.get("char_air"),
            "pin_is_multicard": _b(r.get("pin_is_multicard")),
            "pin_num_cards": _i(r.get("pin_num_cards")),
            "fetched_at": fetched_at,
        })

    n = upsert_rows(db_path, TABLE, raw_rows, key_columns=["pin", "year"])

    # Update parcels with most recent year of characteristics per PIN
    by_pin: dict[str, dict] = {}
    for r in raw_rows:
        if r["pin"] not in by_pin:
            by_pin[r["pin"]] = r

    conn = get_connection(db_path)
    try:
        for pin, r in by_pin.items():
            lot = r["char_land_sf"]
            bldg = r["char_bldg_sf"]
            built_far = round(bldg / lot, 2) if (lot and bldg and lot > 0) else None
            condition = r["char_repair_cnd"] or r["cdu"]
            yr = _i(r["char_yrblt"])
            conn.execute("""
                UPDATE parcels SET
                    lot_size_sf = :lot,
                    building_sf = :bldg,
                    year_built = :yr,
                    condition = :condition,
                    building_classification = :bclass,
                    built_far = :bfar,
                    last_updated_date = :now
                WHERE pin = :pin
            """, {"lot": lot, "bldg": bldg, "yr": yr, "condition": condition,
                  "bclass": r["char_type_resd"], "bfar": built_far,
                  "now": fetched_at, "pin": pin})
        conn.commit()
    finally:
        conn.close()
    return n
