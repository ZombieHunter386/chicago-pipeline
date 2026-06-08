# tests/test_db.py
import sqlite3
from pathlib import Path
import pytest
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


def test_upsert_rows_preserves_columns_on_conflict(tmp_path):
    db = tmp_path / "test.db"
    init_db(db)
    # Create parcels table row with first_seen_date
    conn = get_connection(db)
    conn.execute(
        "INSERT INTO parcels (pin, first_seen_date, last_updated_date) VALUES (?, ?, ?)",
        ("P1", "2026-01-01", "2026-01-01"),
    )
    conn.commit()
    conn.close()
    # Upsert with preserve_columns should not overwrite first_seen_date
    upsert_rows(
        db,
        "parcels",
        [{"pin": "P1", "first_seen_date": "2026-04-19", "last_updated_date": "2026-04-19"}],
        key_columns=["pin"],
        preserve_columns=["first_seen_date"],
    )
    conn = get_connection(db)
    row = conn.execute("SELECT first_seen_date, last_updated_date FROM parcels WHERE pin='P1'").fetchone()
    assert row[0] == "2026-01-01"
    assert row[1] == "2026-04-19"
    conn.close()


def test_init_db_has_condo_rollup_columns(tmp_path):
    """Schema must include the columns condo rollup writes to."""
    db = tmp_path / "ix.db"
    init_db(db)
    conn = get_connection(db)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(parcels)")}
    assert {"is_condo_unit", "is_condo_building", "condo_unit_count"} <= cols
    assert {"min_lot_area_per_unit", "max_units_allowed"} <= cols


def test_init_db_indexes_pin10_and_condo_flags(tmp_path):
    """The condo rollup groups by pin10 and the UI default-filters on
    is_condo_unit; both need indexes for the full-geography fetch."""
    db = tmp_path / "ix.db"
    init_db(db)
    conn = get_connection(db)
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='parcels'"
    ).fetchall()
    names = {r[0] for r in rows}
    assert "idx_parcels_pin10" in names
    assert "idx_parcels_is_condo_unit" in names
    assert "idx_parcels_is_condo_building" in names


def test_init_db_creates_filter_and_sort_indexes(tmp_path):
    """Indexes for filter/sort columns must exist after init_db so the UI's
    default queries don't full-scan at full-geography scale."""
    db = tmp_path / "ix.db"
    init_db(db)
    conn = get_connection(db)
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='parcels'"
    ).fetchall()
    names = {r[0] for r in rows}
    expected = {
        "idx_parcels_zone_class",
        "idx_parcels_property_class",
        "idx_parcels_score",
        "idx_parcels_stage",
        "idx_parcels_last_updated_date",
        "idx_parcels_hold_duration_years",
        "idx_parcels_is_absentee",
        "idx_parcels_is_llc",
        "idx_parcels_tax_delinquent",
        "idx_parcels_consolidation_group_id",
    }
    missing = expected - names
    assert not missing, f"Missing indexes: {missing}"


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


def test_consolidation_groups_has_score_columns(tmp_path):
    """Schema migration: consolidation_groups gets score + score_version
    columns on init_db (so existing data/full.db gains them on next open)."""
    from pipeline.db import init_db, get_connection
    db_path = tmp_path / "schema.db"
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        cols = {row[1] for row in conn.execute(
            "PRAGMA table_info(consolidation_groups)"
        ).fetchall()}
    finally:
        conn.close()
    assert "score" in cols
    assert "score_version" in cols


def test_init_db_creates_outreach_paused_column(tmp_path):
    """init_db should add an outreach_paused column to parcels (via the
    _LATER_COLUMNS migration). Defaults to 0 — the cadence query relies
    on `WHERE outreach_paused = 0` for existing parcels, so a NULL default
    would silently exclude them."""
    from pipeline.db import init_db
    import sqlite3
    p = tmp_path / "t.db"
    init_db(p)
    conn = sqlite3.connect(p)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(parcels)")}
    assert "outreach_paused" in cols
    # Verify the declared default — pragma_table_info returns the literal
    # SQL default text, so DEFAULT 0 shows as the string "0".
    default_val = conn.execute(
        "SELECT dflt_value FROM pragma_table_info('parcels') WHERE name='outreach_paused'"
    ).fetchone()[0]
    assert default_val == "0", f"expected default 0, got {default_val!r}"
    conn.close()


def test_init_db_creates_outreach_gmail_message_id_column(tmp_path):
    """init_db should add a gmail_message_id column to outreach. Replaces
    the prior 'shove it into notes' hack — clean column with one purpose."""
    from pipeline.db import init_db
    import sqlite3
    p = tmp_path / "t.db"
    init_db(p)
    conn = sqlite3.connect(p)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(outreach)")}
    assert "gmail_message_id" in cols
    conn.close()


def test_init_db_creates_outreach_unique_touch_contact_index(tmp_path):
    """init_db creates a partial unique index on
    outreach(pin, touch_number, contact_id) — per-recipient sends mean
    multiple rows per touch are valid (one per addressed contact); only
    the same (pin, touch, contact) tuple must be unique to prevent
    double-sending the same touch to the same recipient."""
    from pipeline.db import init_db
    import sqlite3
    p = tmp_path / "t.db"
    init_db(p)
    conn = sqlite3.connect(p)
    conn.execute("INSERT INTO parcels (pin) VALUES (?)", ("14210010010000",))
    conn.execute(
        "INSERT INTO contacts (pin, email, source) VALUES (?, ?, ?)",
        ("14210010010000", "a@x.com", "manual"),
    )
    cid_a = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO contacts (pin, email, source) VALUES (?, ?, ?)",
        ("14210010010000", "b@x.com", "manual"),
    )
    cid_b = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    # First send to contact A on touch 1
    conn.execute(
        "INSERT INTO outreach (pin, contact_id, touch_number, sent_date) VALUES (?, ?, ?, ?)",
        ("14210010010000", cid_a, 1, "2026-05-15T09:00:00Z"),
    )
    conn.commit()
    # Same (pin, touch, contact_id) MUST fail
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO outreach (pin, contact_id, touch_number, sent_date) VALUES (?, ?, ?, ?)",
            ("14210010010000", cid_a, 1, "2026-05-15T10:00:00Z"),
        )
        conn.commit()
    # Same touch, DIFFERENT contact is fine (per-recipient send)
    conn.execute(
        "INSERT INTO outreach (pin, contact_id, touch_number, sent_date) VALUES (?, ?, ?, ?)",
        ("14210010010000", cid_b, 1, "2026-05-15T09:00:00Z"),
    )
    conn.commit()
    # Different touch, same contact also fine
    conn.execute(
        "INSERT INTO outreach (pin, contact_id, touch_number, sent_date) VALUES (?, ?, ?, ?)",
        ("14210010010000", cid_a, 2, "2026-05-18T09:00:00Z"),
    )
    conn.commit()
    # NULL touch_number rows can repeat (partial index)
    conn.execute("INSERT INTO outreach (pin) VALUES (?)", ("14210010010000",))
    conn.execute("INSERT INTO outreach (pin) VALUES (?)", ("14210010010000",))
    conn.commit()
    conn.close()


def test_raw_ccgis_parcels_has_width_depth_columns(tmp_path):
    p = tmp_path / "t.db"
    init_db(p)
    conn = sqlite3.connect(p)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(raw_ccgis_parcels)")}
    assert "width_ft" in cols
    assert "depth_ft" in cols
    conn.close()


def test_init_db_adds_scoring_profile_columns(tmp_path):
    """Phase 1 of the scoring-profiles plan adds 8 columns to parcels:
    2 lot-geometry, 3 ADU-eligibility, 1 derived sale-price, 2 score
    columns. init_db must be idempotent on a fresh and a pre-existing DB."""
    from pipeline.db import init_db
    import sqlite3
    p = tmp_path / "t.db"
    init_db(p)
    # Re-init on existing DB must not raise.
    init_db(p)

    conn = sqlite3.connect(p)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(parcels)")}
    expected = {
        "lot_width_ft", "lot_depth_ft",
        "adu_eligible", "adu_restriction_text", "adu_has_annual_limits",
        "last_sale_price_recent",
        "score_adu", "score_redev",
    }
    missing = expected - cols
    assert not missing, f"missing columns: {missing}"
    conn.close()
