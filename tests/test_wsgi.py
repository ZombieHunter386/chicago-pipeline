"""Regression tests for the production WSGI entry point.

The deployed DB lives on a persistent volume, downloaded once and never
re-created (scripts/init_db.sh). When a deploy adds a parcels column, the old
volume DB lacks it and the first query SELECTing the new column 500s — which
the UI renders as "no properties". wsgi.py must run init_db at startup so the
_LATER_COLUMNS migration is applied to whatever DB is on the volume.
"""
import importlib
import sqlite3

import pytest

from pipeline.db import init_db
from webapp.app import create_app


def _columns(db_path) -> set[str]:
    conn = sqlite3.connect(db_path)
    try:
        return {r[1] for r in conn.execute("PRAGMA table_info(parcels)")}
    finally:
        conn.close()


@pytest.fixture
def pre_migration_db(tmp_path):
    """A DB shaped like an old deployed snapshot: a parcels table that
    predates the ADU/redev scoring profiles (no score_adu / score_redev)."""
    db_path = tmp_path / "old_prod.db"
    init_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        # Simulate a parcel that should be visible in the UI.
        conn.execute(
            "INSERT INTO parcels (pin, address, is_condo_unit) "
            "VALUES ('14211110071178', '123 Test St', 0)"
        )
        conn.execute("ALTER TABLE parcels DROP COLUMN score_adu")
        conn.execute("ALTER TABLE parcels DROP COLUMN score_redev")
        conn.commit()
    finally:
        conn.close()
    assert "score_adu" not in _columns(db_path)
    assert "score_redev" not in _columns(db_path)
    return db_path


def test_api_parcels_500s_when_score_columns_missing(pre_migration_db):
    # Reproduces the bug: the /api/parcels SELECT references score_adu /
    # score_redev, so against a DB lacking them the query raises and the
    # global handler returns 500 — the UI shows zero properties.
    app = create_app(db_path=pre_migration_db, feature_outreach=False)
    app.testing = True
    resp = app.test_client().get("/api/parcels")
    assert resp.status_code == 500


def test_wsgi_startup_migrates_missing_score_columns(pre_migration_db, monkeypatch):
    # The fix: importing webapp.wsgi runs init_db against DB_PATH, adding the
    # missing columns so the very first request succeeds.
    monkeypatch.setenv("DB_PATH", str(pre_migration_db))
    monkeypatch.delenv("FEATURE_OUTREACH", raising=False)

    import webapp.wsgi as wsgi
    wsgi = importlib.reload(wsgi)

    assert "score_adu" in _columns(pre_migration_db)
    assert "score_redev" in _columns(pre_migration_db)

    wsgi.app.testing = True
    resp = wsgi.app.test_client().get("/api/parcels")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["total"] == 1
    assert data["parcels"][0]["pin"] == "14211110071178"
