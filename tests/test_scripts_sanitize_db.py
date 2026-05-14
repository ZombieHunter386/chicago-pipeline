"""Tests for scripts/sanitize_db_for_r2.py — the safety net that strips
outreach/contacts/waves rows before uploading the DB to R2."""
from __future__ import annotations
import sqlite3
import subprocess
import sys
from pathlib import Path


def _make_db(path: Path) -> None:
    """Build a minimal DB with outreach/contacts/waves rows + one parcel."""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE parcels (pin TEXT PRIMARY KEY, owner_name TEXT);
        CREATE TABLE contacts (contact_id INTEGER PRIMARY KEY, pin TEXT, email TEXT);
        CREATE TABLE outreach (outreach_id INTEGER PRIMARY KEY, pin TEXT, sent_date TEXT);
        CREATE TABLE waves (wave_id INTEGER PRIMARY KEY, notes TEXT);
        INSERT INTO parcels VALUES ('123', 'Acme LLC');
        INSERT INTO contacts VALUES (1, '123', 'a@b.com');
        INSERT INTO outreach VALUES (1, '123', '2026-05-14');
        INSERT INTO waves VALUES (1, 'wave-1');
        """
    )
    conn.commit()
    conn.close()


def test_sanitize_strips_outreach_keeps_parcels(tmp_path: Path) -> None:
    src = tmp_path / "src.db"
    _make_db(src)
    dst = tmp_path / "out.db"

    result = subprocess.run(
        [sys.executable, "scripts/sanitize_db_for_r2.py", str(src), str(dst)],
        capture_output=True, text=True, check=True,
    )
    assert dst.exists(), f"output DB not created. stderr={result.stderr}"

    conn = sqlite3.connect(dst)
    try:
        assert conn.execute("SELECT COUNT(*) FROM parcels").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM outreach").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM waves").fetchone()[0] == 0
    finally:
        conn.close()


def test_sanitize_refuses_to_overwrite_source(tmp_path: Path) -> None:
    src = tmp_path / "src.db"
    _make_db(src)
    result = subprocess.run(
        [sys.executable, "scripts/sanitize_db_for_r2.py", str(src), str(src)],
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "same path" in (result.stderr + result.stdout).lower()
