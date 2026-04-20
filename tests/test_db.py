# tests/test_db.py
import sqlite3
from pathlib import Path
from pipeline.db import init_db, get_connection, upsert_rows


REQUIRED_TABLES = {
    "parcels",
    "consolidation_groups",
    "contacts",
    "outreach",
    "waves",
    "raw_assessor_parcels",
    "raw_assessor_addresses",
    "raw_assessor_characteristics",
    "raw_assessor_values",
    "raw_assessor_sales",
    "raw_assessor_appeals",
    "raw_assessor_exempt",
    "raw_cdp_zoning",
    "raw_cdp_permits",
    "raw_cdp_violations",
    "raw_cdp_vacant",
    "raw_cdp_cta_stations",
    "raw_clerk_delinquent",
    "fetch_log",
}


def test_init_db_creates_all_required_tables(tmp_path):
    db = tmp_path / "test.db"
    init_db(db)
    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    tables = {r[0] for r in rows}
    missing = REQUIRED_TABLES - tables
    assert not missing, f"Missing tables: {missing}"


def test_parcels_table_has_pin_primary_key(tmp_path):
    db = tmp_path / "test.db"
    init_db(db)
    conn = sqlite3.connect(db)
    info = conn.execute("PRAGMA table_info(parcels)").fetchall()
    pk_cols = [c[1] for c in info if c[5] > 0]
    assert pk_cols == ["pin"]


def test_init_db_is_idempotent(tmp_path):
    db = tmp_path / "test.db"
    init_db(db)
    init_db(db)  # second call must not raise


def test_get_connection_enables_foreign_keys(tmp_path):
    db = tmp_path / "test.db"
    init_db(db)
    conn = get_connection(db)
    fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    assert fk == 1


def test_upsert_rows_inserts_new(tmp_path):
    db = tmp_path / "test.db"
    init_db(db)
    rows = [
        {"pin": "X1", "exemption_type": "church", "fetched_at": "2026-04-19"},
        {"pin": "X2", "exemption_type": "school", "fetched_at": "2026-04-19"},
    ]
    n = upsert_rows(db, "raw_assessor_exempt", rows, key_columns=["pin"])
    assert n == 2
    conn = get_connection(db)
    got = conn.execute("SELECT pin, exemption_type FROM raw_assessor_exempt ORDER BY pin").fetchall()
    assert [(r[0], r[1]) for r in got] == [("X1", "church"), ("X2", "school")]


def test_upsert_rows_updates_existing(tmp_path):
    db = tmp_path / "test.db"
    init_db(db)
    upsert_rows(db, "raw_assessor_exempt",
                [{"pin": "X1", "exemption_type": "church", "fetched_at": "2026-04-01"}],
                key_columns=["pin"])
    upsert_rows(db, "raw_assessor_exempt",
                [{"pin": "X1", "exemption_type": "synagogue", "fetched_at": "2026-04-19"}],
                key_columns=["pin"])
    conn = get_connection(db)
    row = conn.execute("SELECT exemption_type, fetched_at FROM raw_assessor_exempt WHERE pin='X1'").fetchone()
    assert row[0] == "synagogue"
    assert row[1] == "2026-04-19"


def test_upsert_rows_empty_list_is_noop(tmp_path):
    db = tmp_path / "test.db"
    init_db(db)
    n = upsert_rows(db, "raw_assessor_exempt", [], key_columns=["pin"])
    assert n == 0


def test_upsert_rows_composite_key(tmp_path):
    db = tmp_path / "test.db"
    init_db(db)
    rows = [
        {"pin": "X1", "year": "2024", "class": "211", "lat": 41.9, "lon": -87.6,
         "pin10": None, "ward_num": None, "zip_code": None, "tax_tif_district_num": None,
         "tax_tif_district_name": None, "township_code": None, "nbhd_code": None,
         "fetched_at": "2026-04-19"},
        {"pin": "X1", "year": "2025", "class": "211", "lat": 41.9, "lon": -87.6,
         "pin10": None, "ward_num": None, "zip_code": None, "tax_tif_district_num": None,
         "tax_tif_district_name": None, "township_code": None, "nbhd_code": None,
         "fetched_at": "2026-04-19"},
    ]
    n = upsert_rows(db, "raw_assessor_parcels", rows, key_columns=["pin", "year"])
    assert n == 2
