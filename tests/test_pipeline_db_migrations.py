from __future__ import annotations
import sqlite3
from pathlib import Path
import pytest
from pipeline.db import init_db


@pytest.fixture
def fresh_db(tmp_path: Path) -> Path:
    db = tmp_path / "test.db"
    init_db(db)
    return db


def test_contacts_has_dead_and_wrong_person_columns(fresh_db: Path):
    with sqlite3.connect(fresh_db) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(contacts)")}
    assert {"dead", "wrong_person", "confidence_pct",
            "enrichment_source", "related_person_name",
            "dead_at", "dead_reason"} <= cols


def test_enrichment_results_table_exists(fresh_db: Path):
    with sqlite3.connect(fresh_db) as conn:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "enrichment_results" in names
    assert "enrichment_jobs" in names
    assert "enrichment_job_pins" in names
    assert "bounce_poll_state" in names


def test_wal_mode_enabled(fresh_db: Path):
    with sqlite3.connect(fresh_db) as conn:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal"


def test_init_db_idempotent(fresh_db: Path):
    # Running init_db again on an existing DB must not error
    # (ALTER TABLE ADD COLUMN raises if column exists).
    init_db(fresh_db)
    init_db(fresh_db)


def test_existing_contacts_rows_get_default_dead_false(tmp_path):
    """Simulate a DB that predates the migration: insert a contact, then
    verify dead defaults to 0 (not NULL)."""
    db = tmp_path / "pre.db"
    init_db(db)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO parcels(pin, owner_name, mail_address) "
            "VALUES ('14321010010000', 'TEST OWNER', '123 MAIN ST')"
        )
        conn.execute(
            "INSERT INTO contacts(pin, email, source) "
            "VALUES ('14321010010000', 'test@example.com', 'manual')"
        )
        conn.commit()
        row = conn.execute(
            "SELECT dead, wrong_person FROM contacts WHERE pin='14321010010000'"
        ).fetchone()
    assert row == (0, 0)


def test_enrichment_tables_accept_inserts(fresh_db: Path):
    """Stronger than the name-exists check: actually insert into each new
    table with FKs enforced and verify the row lands. Catches DDL typos
    AND regression-protects the foreign-key declarations."""
    with sqlite3.connect(fresh_db) as conn:
        # FKs are off by default on a bare sqlite3.connect — enable so
        # the FK clauses on these tables are actually exercised by this
        # test. Production code goes through pipeline.db.get_connection
        # which sets this pragma; tests using bare connect() do not.
        conn.execute("PRAGMA foreign_keys=ON")
        # parcels row needed for FK satisfaction
        conn.execute(
            "INSERT INTO parcels(pin, owner_name, mail_address) "
            "VALUES ('14321010010000', 'TEST', '123 MAIN ST')"
        )
        # enrichment_jobs (no FK)
        conn.execute(
            "INSERT INTO enrichment_jobs(pin_list_json, status) "
            "VALUES ('[]', 'running')"
        )
        job_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        # enrichment_results
        conn.execute(
            "INSERT INTO enrichment_results(pin, job_id, provider, lookup_type, "
            "query_name, raw_response_json, cost_usd, status) "
            "VALUES ('14321010010000', ?, 'tracerfy', 'skip_trace_normal', "
            "'TEST', '{}', 0.10, 'success')",
            (job_id,),
        )
        # enrichment_job_pins
        conn.execute(
            "INSERT INTO enrichment_job_pins(job_id, pin, status) "
            "VALUES (?, '14321010010000', 'done')",
            (job_id,),
        )
        conn.commit()
        # Sanity: all rows present
        assert conn.execute("SELECT COUNT(*) FROM enrichment_jobs").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM enrichment_results").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM enrichment_job_pins").fetchone()[0] == 1


def test_enrichment_results_fk_enforced(fresh_db: Path):
    """Inserting an enrichment_results row with a missing parent pin
    raises IntegrityError. Locks in the FK declaration so future devs
    can't silently drop it."""
    with sqlite3.connect(fresh_db) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO enrichment_results(pin, provider, lookup_type, "
                "query_name, raw_response_json, cost_usd, status) "
                "VALUES ('99999999999999', 'tracerfy', 'skip_trace_normal', "
                "'X', '{}', 0.10, 'success')"
            )


def test_enrichment_job_pins_fk_enforced(fresh_db: Path):
    """Same regression-protection for enrichment_job_pins.job_id."""
    with sqlite3.connect(fresh_db) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        # parcel exists, but job_id does not
        conn.execute(
            "INSERT INTO parcels(pin, owner_name, mail_address) "
            "VALUES ('14321010010000', 'X', 'Y')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO enrichment_job_pins(job_id, pin, status) "
                "VALUES (99999, '14321010010000', 'done')"
            )


def test_bounce_poll_state_singleton_enforced(fresh_db: Path):
    """CHECK (id = 1) must prevent a second row."""
    with sqlite3.connect(fresh_db) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO bounce_poll_state(id) VALUES (2)")


def test_timestamps_use_iso_z_format(fresh_db: Path):
    """The TEXT timestamp columns default to ISO-8601 with Z suffix,
    matching the project's format convention used in cadence + outreach."""
    import re
    iso_z = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
    with sqlite3.connect(fresh_db) as conn:
        conn.execute(
            "INSERT INTO parcels(pin, owner_name, mail_address) "
            "VALUES ('14321010010000', 'X', 'Y')"
        )
        conn.execute(
            "INSERT INTO enrichment_jobs(pin_list_json, status) "
            "VALUES ('[]', 'running')"
        )
        row = conn.execute(
            "SELECT created_at FROM enrichment_jobs"
        ).fetchone()
        assert iso_z.match(row[0]), f"Expected ISO-Z, got {row[0]!r}"
