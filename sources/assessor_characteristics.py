"""Source 1C — Cook County Assessor Improvement Characteristics."""
from __future__ import annotations
from collections import defaultdict
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


def _aggregate_cards(cards: list[dict]) -> dict:
    """Collapse N improvement-card rows for one (pin, year) into a single row.
    Takes ALL fields from the primary card (largest char_bldg_sf) — including
    char_bldg_sf itself. The previous behavior summed bldg_sf across cards
    (main + coach house = combined total), but that diverged from how the
    Chicago building footprints dataset reports per-structure SF, leaving the
    two sources non-comparable for the merge in cdp_building_footprints.
    char_land_sf is still summed because lot area is allocated per-card and
    the per-card values must be combined to get the parcel total.

    We also store the summed value as char_bldg_sf_sum so we can compare
    'sum vs largest vs footprint' per parcel without refetching — useful
    when validating which interpretation matches reality on a given parcel
    type (the largest assumption is likely wrong on commercial mixed-use)."""
    bldg_sfs = [(_f(c.get("char_bldg_sf")) or 0.0, c) for c in cards]
    primary = max(bldg_sfs, key=lambda x: x[0])[1]
    primary_bldg_sf = bldg_sfs[0][0] if len(cards) == 1 else max(s for s, _ in bldg_sfs)
    total_bldg_sf = sum(s for s, _ in bldg_sfs)
    total_land_sf = sum((_f(c.get("char_land_sf")) or 0.0) for c in cards)
    return {
        "pin": primary.get("pin"), "year": primary.get("year"),
        "class": primary.get("class"),
        "char_land_sf": total_land_sf or None,
        "char_bldg_sf": primary_bldg_sf or None,
        "char_bldg_sf_sum": total_bldg_sf or None,
        "char_yrblt": primary.get("char_yrblt"),
        "char_cnst_qlty": primary.get("char_cnst_qlty"),
        "char_repair_cnd": primary.get("char_repair_cnd"),
        "cdu": primary.get("cdu"),
        "char_beds": primary.get("char_beds"),
        "char_rooms": primary.get("char_rooms"),
        "char_fbath": primary.get("char_fbath"),
        "char_hbath": primary.get("char_hbath"),
        "char_type_resd": primary.get("char_type_resd"),
        "char_ext_wall": primary.get("char_ext_wall"),
        "char_heat": primary.get("char_heat"),
        "char_bsmt": primary.get("char_bsmt"),
        "char_bsmt_fin": primary.get("char_bsmt_fin"),
        "char_gar1_att": primary.get("char_gar1_att"),
        "char_gar1_area": primary.get("char_gar1_area"),
        "char_use": primary.get("char_use"),
        "char_site": primary.get("char_site"),
        "char_air": primary.get("char_air"),
        "pin_is_multicard": _b(primary.get("pin_is_multicard")),
        "pin_num_cards": _i(primary.get("pin_num_cards")),
    }


def fetch(geo: GeographyConfig, db_path: Path, client: SocrataClient) -> int:
    fetched_at = datetime.now(UTC).isoformat(timespec="seconds")
    conn = get_connection(db_path)
    try:
        known_pins = {row[0] for row in conn.execute("SELECT pin FROM parcels")}
    finally:
        conn.close()
    if not known_pins:
        return 0

    # Group by (pin, year) so multi-card rows aggregate into a single row
    # whose char_bldg_sf is the sum across cards. The dataset returns one
    # row per improvement card (e.g. main building + coach house); without
    # aggregation we'd keep only one card and undercount built sf.
    by_key: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in client.fetch_by_pins(DATASET_ID, known_pins, order="year DESC"):
        pin = r.get("pin")
        year = r.get("year")
        if pin not in known_pins:
            continue
        by_key[(pin, year)].append(r)

    raw_rows: list[dict] = [
        {**_aggregate_cards(cards), "fetched_at": fetched_at}
        for (_, _), cards in by_key.items()
    ]

    n = upsert_rows(db_path, TABLE, raw_rows, key_columns=["pin", "year"])

    # Update parcels with most recent year of characteristics per PIN
    by_pin: dict[str, dict] = {}
    for r in raw_rows:
        if r["pin"] not in by_pin:
            by_pin[r["pin"]] = r

    # NOTE: lot_size_sf and built_far are NOT written here. The GIS parcel
    # source (ccgis_parcels) is the source of truth for lot_size_sf because
    # it covers condos and vacant lots that have no characteristics record;
    # built_far is recomputed there once both lot and building are known.
    conn = get_connection(db_path)
    try:
        for pin, r in by_pin.items():
            bldg = r["char_bldg_sf"]
            condition = r["char_repair_cnd"] or r["cdu"]
            yr = _i(r["char_yrblt"])
            conn.execute("""
                UPDATE parcels SET
                    building_sf = :bldg,
                    year_built = :yr,
                    condition = :condition,
                    building_classification = :bclass,
                    last_updated_date = :now
                WHERE pin = :pin
            """, {"bldg": bldg, "yr": yr, "condition": condition,
                  "bclass": r["char_type_resd"],
                  "now": fetched_at, "pin": pin})
        conn.commit()
    finally:
        conn.close()
    return n
