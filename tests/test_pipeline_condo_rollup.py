from pathlib import Path

from pipeline.condo_rollup import rollup_condos
from pipeline.db import init_db, get_connection


def _seed(db_path: Path, rows: list[dict]):
    """Insert minimal parcel rows for testing."""
    conn = get_connection(db_path)
    try:
        for r in rows:
            conn.execute(
                """INSERT INTO parcels
                       (pin, pin10, property_class, assessed_total,
                        assessed_land, assessed_building, estimated_annual_tax,
                        building_sf,
                        first_seen_date, last_updated_date, stage)
                   VALUES (:pin, :pin10, :cls, :at, :al, :ab, :etax,
                           :bsf,
                           '2026-04-26', '2026-04-26', 'scored')""",
                {**r, "bsf": r.get("bsf")},
            )
        conn.commit()
    finally:
        conn.close()


def _seed_raw_chars(db_path: Path, rows: list[dict]):
    conn = get_connection(db_path)
    try:
        for r in rows:
            conn.execute(
                "INSERT INTO raw_assessor_characteristics "
                "  (pin, year, char_bldg_sf, fetched_at) "
                "VALUES (:pin, '2025', :bsf, '2026-04-26')",
                r,
            )
        conn.commit()
    finally:
        conn.close()


def _seed_raw_values(db_path: Path, rows: list[dict]):
    conn = get_connection(db_path)
    try:
        for r in rows:
            conn.execute(
                "INSERT INTO raw_assessor_values "
                "  (pin, year, board_tot, board_land, board_bldg, "
                "   certified_tot, certified_land, certified_bldg, "
                "   mailed_tot, mailed_land, mailed_bldg, fetched_at) "
                "VALUES (:pin, '2025', :tot, :land, :bldg, "
                "        :tot, :land, :bldg, :tot, :land, :bldg, '2026-04-26')",
                r,
            )
        conn.commit()
    finally:
        conn.close()


def test_rollup_picks_lowest_pin_as_rep_and_sums_av(tmp_path):
    db = tmp_path / "t.db"
    init_db(db)
    _seed(db, [
        {"pin": "10000000000003", "pin10": "1000000000", "cls": "299",
         "at": 100000, "al": 30000, "ab": 70000, "etax": 6700},
        {"pin": "10000000000001", "pin10": "1000000000", "cls": "299",
         "at": 200000, "al": 60000, "ab": 140000, "etax": 13400},
        {"pin": "10000000000002", "pin10": "1000000000", "cls": "299",
         "at": 150000, "al": 45000, "ab": 105000, "etax": 10050},
    ])

    rollup_condos(db)

    conn = get_connection(db)
    rep = conn.execute(
        "SELECT pin, is_condo_building, is_condo_unit, condo_unit_count, "
        "       assessed_total, assessed_land, assessed_building, estimated_annual_tax "
        "FROM parcels WHERE pin='10000000000001'"
    ).fetchone()
    assert rep["is_condo_building"] == 1
    assert rep["is_condo_unit"] == 0
    assert rep["condo_unit_count"] == 3
    assert rep["assessed_total"] == 450000
    assert rep["assessed_land"] == 135000
    assert rep["assessed_building"] == 315000
    assert rep["estimated_annual_tax"] == 30150

    units = conn.execute(
        "SELECT pin, is_condo_unit, is_condo_building FROM parcels "
        "WHERE pin IN ('10000000000002', '10000000000003') ORDER BY pin"
    ).fetchall()
    assert all(u["is_condo_unit"] == 1 for u in units)
    assert all(u["is_condo_building"] == 0 for u in units)


def test_rollup_skips_non_condo_pin10s(tmp_path):
    db = tmp_path / "t.db"
    init_db(db)
    _seed(db, [
        {"pin": "20000000000000", "pin10": "2000000000", "cls": "211",
         "at": 500000, "al": 100000, "ab": 400000, "etax": 33500},
    ])
    rollup_condos(db)

    conn = get_connection(db)
    p = conn.execute("SELECT * FROM parcels WHERE pin='20000000000000'").fetchone()
    assert p["is_condo_unit"] == 0
    assert p["is_condo_building"] == 0
    assert p["condo_unit_count"] is None
    assert p["assessed_total"] == 500000


def test_rollup_handles_single_unit_condo(tmp_path):
    """Townhouse-style condo with only one PIN under its pin10 should
    be flagged is_condo_building=1, condo_unit_count=1."""
    db = tmp_path / "t.db"
    init_db(db)
    _seed(db, [
        {"pin": "30000000000001", "pin10": "3000000000", "cls": "299",
         "at": 80000, "al": 20000, "ab": 60000, "etax": 5360},
    ])
    rollup_condos(db)
    conn = get_connection(db)
    p = conn.execute("SELECT * FROM parcels WHERE pin='30000000000001'").fetchone()
    assert p["is_condo_building"] == 1
    assert p["is_condo_unit"] == 0
    assert p["condo_unit_count"] == 1
    assert p["assessed_total"] == 80000


def test_rollup_is_idempotent(tmp_path):
    """Running twice in a row must produce the same result, not double-sum."""
    db = tmp_path / "t.db"
    init_db(db)
    _seed(db, [
        {"pin": "40000000000001", "pin10": "4000000000", "cls": "299",
         "at": 100000, "al": 30000, "ab": 70000, "etax": 6700},
        {"pin": "40000000000002", "pin10": "4000000000", "cls": "299",
         "at": 100000, "al": 30000, "ab": 70000, "etax": 6700},
    ])
    _seed_raw_values(db, [
        {"pin": "40000000000001", "tot": 100000, "land": 30000, "bldg": 70000},
        {"pin": "40000000000002", "tot": 100000, "land": 30000, "bldg": 70000},
    ])
    rollup_condos(db)
    rollup_condos(db)
    conn = get_connection(db)
    rep = conn.execute(
        "SELECT condo_unit_count, assessed_total FROM parcels WHERE pin='40000000000001'"
    ).fetchone()
    assert rep["condo_unit_count"] == 2
    assert rep["assessed_total"] == 200000


def test_rollup_sums_building_sf_onto_rep(tmp_path):
    """Each condo unit's char_bldg_sf is its own interior sf; the rep's
    building_sf must sum across all units in the pin10 to reflect the
    whole building's footprint."""
    db = tmp_path / "t.db"
    init_db(db)
    _seed(db, [
        {"pin": "60000000000001", "pin10": "6000000000", "cls": "299",
         "at": 100000, "al": 30000, "ab": 70000, "etax": 6700, "bsf": 1200},
        {"pin": "60000000000002", "pin10": "6000000000", "cls": "299",
         "at": 100000, "al": 30000, "ab": 70000, "etax": 6700, "bsf": 1200},
        {"pin": "60000000000003", "pin10": "6000000000", "cls": "299",
         "at": 100000, "al": 30000, "ab": 70000, "etax": 6700, "bsf": 1500},
    ])
    rollup_condos(db)
    conn = get_connection(db)
    rep = conn.execute(
        "SELECT building_sf, condo_unit_count "
        "FROM parcels WHERE pin='60000000000001'"
    ).fetchone()
    assert rep["building_sf"] == 3900.0  # 1200 + 1200 + 1500
    assert rep["condo_unit_count"] == 3


def test_rollup_building_sf_idempotent_via_raw_restore(tmp_path):
    """Re-running rollup must not double-sum building_sf. The rep's per-PIN
    bldg_sf is restored from raw_assessor_characteristics before summing."""
    db = tmp_path / "t.db"
    init_db(db)
    _seed(db, [
        {"pin": "70000000000001", "pin10": "7000000000", "cls": "299",
         "at": 100000, "al": 30000, "ab": 70000, "etax": 6700, "bsf": 1000},
        {"pin": "70000000000002", "pin10": "7000000000", "cls": "299",
         "at": 100000, "al": 30000, "ab": 70000, "etax": 6700, "bsf": 1000},
    ])
    _seed_raw_values(db, [
        {"pin": "70000000000001", "tot": 100000, "land": 30000, "bldg": 70000},
        {"pin": "70000000000002", "tot": 100000, "land": 30000, "bldg": 70000},
    ])
    _seed_raw_chars(db, [
        {"pin": "70000000000001", "bsf": 1000},
        {"pin": "70000000000002", "bsf": 1000},
    ])
    rollup_condos(db)
    rollup_condos(db)
    conn = get_connection(db)
    rep = conn.execute(
        "SELECT building_sf FROM parcels WHERE pin='70000000000001'"
    ).fetchone()
    assert rep["building_sf"] == 2000.0  # NOT 4000 (would be if double-summed)


def test_rollup_includes_mixed_classes_under_condo_pin10(tmp_path):
    """A pin10 with both class-299 and class-290 (parking condo) constituents
    is one building — roll all of them up together."""
    db = tmp_path / "t.db"
    init_db(db)
    _seed(db, [
        {"pin": "50000000000001", "pin10": "5000000000", "cls": "299",
         "at": 150000, "al": 50000, "ab": 100000, "etax": 10050},
        {"pin": "50000000000002", "pin10": "5000000000", "cls": "290",
         "at": 5000, "al": 5000, "ab": 0, "etax": 335},
    ])
    rollup_condos(db)
    conn = get_connection(db)
    rep = conn.execute(
        "SELECT condo_unit_count, assessed_total FROM parcels WHERE pin='50000000000001'"
    ).fetchone()
    assert rep["condo_unit_count"] == 2
    assert rep["assessed_total"] == 155000
