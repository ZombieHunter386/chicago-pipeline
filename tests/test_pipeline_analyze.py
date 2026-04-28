"""Tests for pipeline/analyze.py — the historical-analysis script that derives
initial scoring weights from permit history."""
from pathlib import Path
from datetime import datetime, UTC

import pandas as pd

from pipeline import analyze
from pipeline.db import init_db, upsert_rows


def test_signals_registry_shape():
    """SIGNALS is the single source of truth for what features the model sees.
    Every entry must be a 3-tuple of (column_name, kind, source_table) where
    kind is 'continuous' or 'binary'."""
    assert len(analyze.SIGNALS) > 0
    for entry in analyze.SIGNALS:
        assert len(entry) == 3
        col, kind, source = entry
        assert isinstance(col, str) and col
        assert kind in ("continuous", "binary")
        assert source == "parcels", \
            f"{col}: only the parcels table is supported in v1"


def test_signals_excludes_known_bad_columns():
    """tax_delinquent has 0% population on data/full.db (CSV is a stub).
    has_vacancy_report uses a defunct legacy dataset. Both must NOT be in
    the v1 feature set."""
    cols = [s[0] for s in analyze.SIGNALS]
    assert "tax_delinquent" not in cols
    assert "has_vacancy_report" not in cols


def test_analyze_entry_point_exists():
    """The orchestrator must accept (db_path, geo, scoring_yaml_path, report_md_path)."""
    import inspect
    sig = inspect.signature(analyze.analyze)
    assert list(sig.parameters) == ["db_path", "geo", "scoring_yaml_path", "report_md_path"]


def _build_analyze_db(tmp_path: Path, parcels: list[dict], permits: list[dict] | None = None,
                      values: list[dict] | None = None,
                      exempt: list[dict] | None = None) -> Path:
    """Create a fresh SQLite DB with init_db() schema and insert the given rows
    directly into raw_/parcels tables. No fetch flow — these tests don't care
    about Socrata wiring; they care about the analyze logic."""
    db_path = tmp_path / "analyze.db"
    init_db(db_path)
    now = datetime.now(UTC).isoformat(timespec="seconds")
    if parcels:
        upsert_rows(db_path, "parcels",
                    [{**p, "last_fetched_date": now} for p in parcels],
                    key_columns=["pin"])
    if permits:
        upsert_rows(db_path, "raw_cdp_permits",
                    [{**p, "fetched_at": now} for p in permits],
                    key_columns=["permit_number"])
    if values:
        upsert_rows(db_path, "raw_assessor_values",
                    [{**v, "fetched_at": now} for v in values],
                    key_columns=["pin", "year"])
    if exempt:
        upsert_rows(db_path, "raw_assessor_exempt",
                    [{**e, "fetched_at": now} for e in exempt],
                    key_columns=["pin"])
    return db_path


def test_identify_positive_examples_filters_permit_types(tmp_path):
    """Only NEW CONSTRUCTION and WRECKING/DEMOLITION qualify. Reroofs,
    renovations, and electrical permits don't move parcels into the positive
    set — those happen routinely on long-held property without redevelopment."""
    parcels = [
        {"pin": "14210010010000", "address": "100 W DIVERSEY PKWY",
         "lat": 41.94001, "lng": -87.65001},
        {"pin": "14210010020000", "address": "200 N HALSTED",
         "lat": 41.93001, "lng": -87.66001},
        {"pin": "14210010030000", "address": "300 W FULLERTON AVE",
         "lat": 41.92501, "lng": -87.65501},
    ]
    permits = [
        # PIN 1 — NEW CONSTRUCTION 2018, qualifies
        {"permit_number": "p1", "permit_type": "PERMIT - NEW CONSTRUCTION",
         "issue_date": "2018-05-12",
         "street_number": "100", "street_direction": "W", "street_name": "DIVERSEY PKWY",
         "latitude": 41.94001, "longitude": -87.65001},
        # PIN 1 — earlier demo 2014, qualifies and supersedes the 2018 one
        # (we want the EVENT year — earliest qualifying permit)
        {"permit_number": "p2", "permit_type": "PERMIT - WRECKING/DEMOLITION",
         "issue_date": "2014-08-22",
         "street_number": "100", "street_direction": "W", "street_name": "DIVERSEY PKWY",
         "latitude": 41.94001, "longitude": -87.65001},
        # PIN 2 — RENOVATION ONLY, does NOT qualify
        {"permit_number": "p3", "permit_type": "PERMIT - RENOVATION/ALTERATION",
         "issue_date": "2020-01-15",
         "street_number": "200", "street_direction": "N", "street_name": "HALSTED",
         "latitude": 41.93001, "longitude": -87.66001},
        # PIN 3 — ELECTRIC WIRING, does NOT qualify
        {"permit_number": "p4", "permit_type": "PERMIT - ELECTRIC WIRING",
         "issue_date": "2021-06-01",
         "street_number": "300", "street_direction": "W", "street_name": "FULLERTON AVE",
         "latitude": 41.92501, "longitude": -87.65501},
    ]
    db_path = _build_analyze_db(tmp_path, parcels, permits)

    result = analyze.identify_positive_examples(db_path)

    # Only PIN 1 qualifies, and the year is the earlier 2014 demo, not the 2018 build.
    assert result == {"14210010010000": 2014}


def test_identify_positive_examples_handles_address_match(tmp_path):
    """Permits with no lat/lng but matching street address should still match
    via the address-first matcher (Tier 1)."""
    parcels = [
        {"pin": "14210010010000", "address": "100 W DIVERSEY PKWY",
         "lat": 41.94001, "lng": -87.65001},
    ]
    permits = [
        # No lat/lng, but the street_number/direction/name reconstruct to the parcel address.
        {"permit_number": "p1", "permit_type": "PERMIT - NEW CONSTRUCTION",
         "issue_date": "2019-07-04",
         "street_number": "100", "street_direction": "W", "street_name": "DIVERSEY PKWY",
         "latitude": None, "longitude": None},
    ]
    db_path = _build_analyze_db(tmp_path, parcels, permits)
    result = analyze.identify_positive_examples(db_path)
    assert result == {"14210010010000": 2019}


def test_identify_positive_examples_returns_empty_when_no_qualifying_permits(tmp_path):
    parcels = [{"pin": "14210010010000", "address": "100 W DIVERSEY PKWY",
                "lat": 41.94001, "lng": -87.65001}]
    permits = [
        {"permit_number": "p1", "permit_type": "PERMIT - RENOVATION/ALTERATION",
         "issue_date": "2020-01-01",
         "street_number": "100", "street_direction": "W", "street_name": "DIVERSEY PKWY",
         "latitude": 41.94001, "longitude": -87.65001},
    ]
    db_path = _build_analyze_db(tmp_path, parcels, permits)
    assert analyze.identify_positive_examples(db_path) == {}


def _parcel_row(pin, **overrides):
    """Helper: minimum-viable parcels row with sensible defaults so individual
    tests only spell out the fields they care about."""
    base = {
        "pin": pin, "address": f"{pin[-4:]} W FAKE ST", "lat": 41.93, "lng": -87.65,
        "lot_size_sf": 5000.0, "hold_duration_years": 10.0,
        "max_far": 2.0, "far_gap_delta": 0.5, "land_building_ratio": 0.4,
        "estimated_annual_tax": 12000.0, "tax_increase_pct_5yr": 15.0,
        "cta_distance_ft": 2000.0, "appeal_count": 1, "open_violations_count": 0,
        "years_since_last_permit": 5.0, "vacant_violations_count": 0,
        "scofflaw_appearances_count": 0,
        "is_absentee": 0, "is_llc": 0, "allows_multifamily_by_right": 1,
        "is_scofflaw": 0, "is_condo_unit": 0, "zone_class": "RM-5",
    }
    base.update(overrides)
    return base


def test_build_training_table_basic_shape(tmp_path):
    parcels = [_parcel_row("14210010010000"), _parcel_row("14210010020000")]
    db_path = _build_analyze_db(tmp_path, parcels)
    positives = {"14210010010000": 2018}
    df = analyze.build_training_table(db_path, positives)
    assert len(df) == 2
    assert "label" in df.columns
    # Order isn't guaranteed; check by PIN.
    by_pin = df.set_index("pin")
    assert by_pin.loc["14210010010000", "label"] == 1
    assert by_pin.loc["14210010020000", "label"] == 0
    # Every signal column must be present.
    for col, _kind, _src in analyze.SIGNALS:
        assert col in df.columns


def test_build_training_table_drops_tax_exempt(tmp_path):
    parcels = [_parcel_row("14210010010000"), _parcel_row("14210010020000")]
    exempt = [{"pin": "14210010020000", "exemption_type": "Religious"}]
    db_path = _build_analyze_db(tmp_path, parcels, exempt=exempt)
    df = analyze.build_training_table(db_path, positive_pins={})
    assert df["pin"].tolist() == ["14210010010000"]


def test_build_training_table_drops_pd_zoned(tmp_path):
    parcels = [
        _parcel_row("14210010010000", zone_class="RM-5"),
        _parcel_row("14210010020000", zone_class="PD 555", max_far=None,
                    allows_multifamily_by_right=None, far_gap_delta=None),
    ]
    db_path = _build_analyze_db(tmp_path, parcels)
    df = analyze.build_training_table(db_path, positive_pins={})
    assert df["pin"].tolist() == ["14210010010000"]


def test_build_training_table_drops_condo_units_keeps_building_reps(tmp_path):
    parcels = [
        _parcel_row("14210010010000", is_condo_unit=0),
        _parcel_row("14210010020000", is_condo_unit=1),
    ]
    db_path = _build_analyze_db(tmp_path, parcels)
    df = analyze.build_training_table(db_path, positive_pins={})
    assert df["pin"].tolist() == ["14210010010000"]


def test_build_training_table_imputes_nulls(tmp_path):
    """Continuous NULLs become the training-set median; binary NULLs become 0.
    Imputed cells are tracked so the report can disclose the imputation rate."""
    parcels = [
        _parcel_row("14210010010000", lot_size_sf=4000.0, is_absentee=1),
        _parcel_row("14210010020000", lot_size_sf=8000.0, is_absentee=None),
        _parcel_row("14210010030000", lot_size_sf=None,    is_absentee=None),
    ]
    db_path = _build_analyze_db(tmp_path, parcels)
    df = analyze.build_training_table(db_path, positive_pins={})
    # Median of (4000, 8000) = 6000
    by_pin = df.set_index("pin")
    assert by_pin.loc["14210010030000", "lot_size_sf"] == 6000.0
    # Binary NULLs go to 0
    assert by_pin.loc["14210010020000", "is_absentee"] == 0
    assert by_pin.loc["14210010030000", "is_absentee"] == 0
    # Imputation rate exposed via attrs (consumed by the report writer)
    rates = df.attrs["imputation_rates"]
    assert rates["lot_size_sf"]["pct"] == round(100 / 3, 1)  # 1 of 3 imputed
    assert rates["is_absentee"]["pct"] == round(200 / 3, 1)  # 2 of 3 imputed
