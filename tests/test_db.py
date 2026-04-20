# tests/test_db.py
import sqlite3
from pathlib import Path
from pipeline.db import init_db, get_connection


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
