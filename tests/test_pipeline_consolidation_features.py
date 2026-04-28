"""Tests for pipeline/consolidation_features.py — aggregates a consolidation
group's constituent-parcel signals into a single feature dict that mirrors
the parcels-row shape Score and Analyze consume."""
from datetime import datetime, UTC, date
import json

from pipeline import consolidation_features
from pipeline.db import init_db, upsert_rows, get_connection


def _build_db_with_group(tmp_path, parcels, group_id, group_pins,
                         combined_lot=None, combined_bldg=None,
                         owner_name="TEST OWNER"):
    """Build a synthetic DB with parcels + a single consolidation group."""
    db_path = tmp_path / "agg.db"
    init_db(db_path)
    now = datetime.now(UTC).isoformat(timespec="seconds")
    upsert_rows(db_path, "parcels",
                [{**p, "last_fetched_date": now} for p in parcels],
                key_columns=["pin"])
    conn = get_connection(db_path)
    try:
        conn.execute("""
            INSERT INTO consolidation_groups
              (group_id, pins, combined_lot_size_sf, combined_building_sf,
               owner_name, detected_date)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (group_id, json.dumps(group_pins), combined_lot, combined_bldg,
              owner_name, date.today().isoformat()))
        conn.commit()
    finally:
        conn.close()
    return db_path


def test_derive_group_features_aggregates_per_rule(tmp_path):
    """Two adjacent same-owner parcels with mixed signal values; verify each
    aggregation rule produces the documented value."""
    parcels = [
        {"pin": "PIN_A", "lot_size_sf": 3000.0, "building_sf": 2000.0,
         "hold_duration_years": 5.0, "estimated_annual_tax": 8000.0,
         "tax_increase_pct_5yr": 20.0, "cta_distance_ft": 1000.0,
         "appeal_count": 2, "open_violations_count": 1,
         "years_since_last_permit": 3.0, "vacant_violations_count": 0,
         "scofflaw_appearances_count": 0,
         "is_absentee": 1, "is_llc": 1, "is_scofflaw": 0,
         "allows_multifamily_by_right": 1, "max_far": 2.5,
         "assessed_land": 50000.0, "assessed_total": 100000.0},
        {"pin": "PIN_B", "lot_size_sf": 4000.0, "building_sf": 3000.0,
         "hold_duration_years": 2.0, "estimated_annual_tax": 12000.0,
         "tax_increase_pct_5yr": 10.0, "cta_distance_ft": 1500.0,
         "appeal_count": 1, "open_violations_count": 3,
         "years_since_last_permit": 7.0, "vacant_violations_count": 1,
         "scofflaw_appearances_count": 0,
         "is_absentee": 0, "is_llc": 1, "is_scofflaw": 0,
         "allows_multifamily_by_right": 0, "max_far": 1.5,
         "assessed_land": 80000.0, "assessed_total": 200000.0},
    ]
    # Combined values written by consolidate.py would be lot=7000, bldg=5000.
    db_path = _build_db_with_group(tmp_path, parcels, group_id=1,
                                   group_pins=["PIN_A", "PIN_B"],
                                   combined_lot=7000.0, combined_bldg=5000.0)
    f = consolidation_features.derive_group_features(group_id=1, db_path=db_path)

    # Direct from the group row
    assert f["lot_size_sf"] == 7000.0
    assert f["building_sf"] == 5000.0

    # MIN rule: hold_duration_years, cta_distance_ft, years_since_last_permit
    assert f["hold_duration_years"] == 2.0
    assert f["cta_distance_ft"] == 1000.0
    assert f["years_since_last_permit"] == 3.0

    # SUM rule
    assert f["estimated_annual_tax"] == 20000.0
    assert f["appeal_count"] == 3
    assert f["open_violations_count"] == 4
    assert f["vacant_violations_count"] == 1
    assert f["scofflaw_appearances_count"] == 0

    # Weighted AVG by assessed_total: (20*100000 + 10*200000) / 300000 = 13.333…
    assert round(f["tax_increase_pct_5yr"], 4) == round((20*100000 + 10*200000) / 300000, 4)

    # MAX rule on binary signals
    assert f["is_absentee"] == 1     # PIN_A=1
    assert f["is_llc"] == 1          # both
    assert f["is_scofflaw"] == 0
    assert f["allows_multifamily_by_right"] == 1   # PIN_A=1

    # MAX on numeric: max_far
    assert f["max_far"] == 2.5

    # Recomputed: far_gap_delta = MAX(max_far) - combined_building_sf / combined_lot_size_sf
    # = 2.5 - 5000/7000 = 2.5 - 0.7142857 = 1.7857142
    assert round(f["far_gap_delta"], 4) == round(2.5 - 5000.0/7000.0, 4)

    # Recomputed: land_building_ratio = SUM(assessed_land) / SUM(assessed_total)
    # = (50000 + 80000) / (100000 + 200000) = 130000/300000 = 0.4333…
    assert round(f["land_building_ratio"], 4) == round(130000/300000, 4)


def test_derive_group_features_handles_all_null_signal(tmp_path):
    """Signals where ALL constituents are NULL must yield None — Score's
    normalize_signal handles None via the neutral-imputation path."""
    parcels = [
        {"pin": "PIN_A", "lot_size_sf": 3000.0, "estimated_annual_tax": None},
        {"pin": "PIN_B", "lot_size_sf": 4000.0, "estimated_annual_tax": None},
    ]
    db_path = _build_db_with_group(tmp_path, parcels, group_id=1,
                                   group_pins=["PIN_A", "PIN_B"],
                                   combined_lot=7000.0)
    f = consolidation_features.derive_group_features(group_id=1, db_path=db_path)
    assert f["estimated_annual_tax"] is None


def test_derive_group_features_skips_recomputation_when_assessed_total_is_zero(tmp_path):
    """Defensive: if SUM(assessed_total) == 0, land_building_ratio is None
    (avoid div-by-zero). Same for tax_increase_pct_5yr weighted avg."""
    parcels = [
        {"pin": "PIN_A", "lot_size_sf": 3000.0, "assessed_total": 0.0,
         "assessed_land": 0.0, "tax_increase_pct_5yr": 5.0},
        {"pin": "PIN_B", "lot_size_sf": 4000.0, "assessed_total": 0.0,
         "assessed_land": 0.0, "tax_increase_pct_5yr": 10.0},
    ]
    db_path = _build_db_with_group(tmp_path, parcels, group_id=1,
                                   group_pins=["PIN_A", "PIN_B"],
                                   combined_lot=7000.0)
    f = consolidation_features.derive_group_features(group_id=1, db_path=db_path)
    assert f["land_building_ratio"] is None
    assert f["tax_increase_pct_5yr"] is None


def test_derive_group_features_recomputes_far_gap_when_lot_size_is_present(tmp_path):
    """far_gap_delta needs MAX(max_far), combined_building_sf, combined_lot_size_sf.
    If any is missing, return None."""
    parcels = [
        {"pin": "PIN_A", "lot_size_sf": 3000.0, "building_sf": None, "max_far": 2.5},
        {"pin": "PIN_B", "lot_size_sf": 4000.0, "building_sf": None, "max_far": 1.5},
    ]
    db_path = _build_db_with_group(tmp_path, parcels, group_id=1,
                                   group_pins=["PIN_A", "PIN_B"],
                                   combined_lot=7000.0, combined_bldg=None)
    f = consolidation_features.derive_group_features(group_id=1, db_path=db_path)
    assert f["far_gap_delta"] is None
