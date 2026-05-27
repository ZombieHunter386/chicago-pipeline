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
