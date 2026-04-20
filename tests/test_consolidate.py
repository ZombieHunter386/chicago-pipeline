# tests/test_consolidate.py
import json
from pipeline.db import get_connection, init_db
from pipeline.consolidate import consolidate


def _seed(db_path, rows):
    conn = get_connection(db_path)
    try:
        for r in rows:
            conn.execute("""
                INSERT INTO parcels (pin, lat, lng, lot_size_sf, owner_name, mail_address,
                                     first_seen_date, last_fetched_date, last_updated_date, stage)
                VALUES (:pin, :lat, :lng, :lot, :owner, :mail, '2026-04-19', '2026-04-19', '2026-04-19', 'scored')
            """, r)
        conn.commit()
    finally:
        conn.close()


def test_consolidate_groups_same_owner_adjacent(db_path):
    _seed(db_path, [
        {"pin": "P1", "lat": 41.9400, "lng": -87.6500, "lot": 3000,
         "owner": "ACME LLC", "mail": "100 Main St"},
        {"pin": "P2", "lat": 41.9401, "lng": -87.6501, "lot": 3000,
         "owner": "ACME LLC", "mail": "100 Main St"},
        {"pin": "P3", "lat": 41.9500, "lng": -87.6300, "lot": 3000,
         "owner": "ACME LLC", "mail": "100 Main St"},   # same owner but far
        {"pin": "P4", "lat": 41.9402, "lng": -87.6502, "lot": 3000,
         "owner": "OTHER LLC", "mail": "200 Main St"},  # different owner
    ])
    n_groups = consolidate(db_path)
    assert n_groups == 1

    conn = get_connection(db_path)
    groups = conn.execute("SELECT group_id, pins, combined_lot_size_sf, owner_name FROM consolidation_groups").fetchall()
    assert len(groups) == 1
    g = groups[0]
    pins = sorted(json.loads(g["pins"]))
    assert pins == ["P1", "P2"]
    assert g["combined_lot_size_sf"] == 6000
    assert g["owner_name"] == "ACME LLC"

    links = conn.execute("""
        SELECT pin, consolidation_group_id FROM parcels WHERE consolidation_group_id IS NOT NULL
        ORDER BY pin
    """).fetchall()
    assert [l["pin"] for l in links] == ["P1", "P2"]
    assert {l["consolidation_group_id"] for l in links} == {g["group_id"]}


def test_consolidate_clusters_transitively(db_path):
    """Single-link clustering: P1-P2 within 200ft, P2-P3 within 200ft,
    but P1-P3 >200ft — all three must still cluster via the chain."""
    # At Chicago latitude, 0.0005° lat ≈ 182 ft; 0.0010° lat ≈ 364 ft.
    _seed(db_path, [
        {"pin": "A1", "lat": 41.9400, "lng": -87.6500, "lot": 2000,
         "owner": "CHAIN LLC", "mail": "100 Main St"},
        {"pin": "A2", "lat": 41.9405, "lng": -87.6500, "lot": 2000,
         "owner": "CHAIN LLC", "mail": "100 Main St"},
        {"pin": "A3", "lat": 41.9410, "lng": -87.6500, "lot": 2000,
         "owner": "CHAIN LLC", "mail": "100 Main St"},
    ])
    n = consolidate(db_path)
    assert n == 1
    conn = get_connection(db_path)
    g = conn.execute("SELECT pins, combined_lot_size_sf FROM consolidation_groups").fetchone()
    assert sorted(json.loads(g["pins"])) == ["A1", "A2", "A3"]
    assert g["combined_lot_size_sf"] == 6000


def test_consolidate_is_idempotent(db_path):
    """Running twice produces the same grouping — prior rows wiped, not duplicated."""
    _seed(db_path, [
        {"pin": "I1", "lat": 41.9400, "lng": -87.6500, "lot": 3000,
         "owner": "IDEM LLC", "mail": "1 Park"},
        {"pin": "I2", "lat": 41.9401, "lng": -87.6501, "lot": 3000,
         "owner": "IDEM LLC", "mail": "1 Park"},
    ])
    consolidate(db_path)
    consolidate(db_path)
    conn = get_connection(db_path)
    rows = conn.execute("SELECT pins FROM consolidation_groups").fetchall()
    assert len(rows) == 1
    linked = conn.execute(
        "SELECT COUNT(*) FROM parcels WHERE consolidation_group_id IS NOT NULL"
    ).fetchone()[0]
    assert linked == 2
