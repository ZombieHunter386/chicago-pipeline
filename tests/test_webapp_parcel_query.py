import sqlite3
from webapp.parcel_query import build_parcel_query


def _run(db_path, sql, params):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def _scalar(db_path, sql, *params):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(sql, params).fetchone()[0]
    finally:
        conn.close()


def test_empty_filters_returns_all_with_default_sort(populated_db_path):
    sql, params = build_parcel_query(filters={}, stage=None, limit=20, offset=0)
    rows = _run(populated_db_path, sql, params)
    assert len(rows) == 20  # pagination cap
    # Default sort is last_updated_date DESC, then hold_duration_years DESC
    assert "ORDER BY" in sql
    assert "last_updated_date DESC" in sql


def test_checkbox_filter_is_absentee(populated_db_path):
    sql, params = build_parcel_query(
        filters={"is_absentee": True}, stage=None, limit=1000, offset=0
    )
    rows = _run(populated_db_path, sql, params)
    expected = _scalar(
        populated_db_path,
        "SELECT COUNT(*) FROM parcels WHERE is_absentee = 1 AND is_condo_unit = 0",
    )
    assert len(rows) == expected
    assert all(r["is_absentee"] == 1 for r in rows)


def test_range_filter_hold_duration_min_only(populated_db_path):
    sql, params = build_parcel_query(
        filters={"hold_duration_years": {"min": 20}},
        stage=None, limit=1000, offset=0,
    )
    rows = _run(populated_db_path, sql, params)
    assert all(r["hold_duration_years"] >= 20 for r in rows)
    assert len(rows) > 0


def test_range_filter_hold_duration_min_and_max(populated_db_path):
    sql, params = build_parcel_query(
        filters={"hold_duration_years": {"min": 5, "max": 10}},
        stage=None, limit=1000, offset=0,
    )
    rows = _run(populated_db_path, sql, params)
    assert all(5 <= r["hold_duration_years"] <= 10 for r in rows)


def test_dropdown_filter_property_class(populated_db_path):
    sql, params = build_parcel_query(
        filters={"property_class": "299"},
        stage=None, limit=1000, offset=0,
    )
    rows = _run(populated_db_path, sql, params)
    assert all(r["property_class"] == "299" for r in rows)
    assert len(rows) > 0


def test_stage_filter(populated_db_path):
    sql, params = build_parcel_query(filters={}, stage="scored", limit=1000, offset=0)
    rows = _run(populated_db_path, sql, params)
    assert all(r["stage"] == "scored" for r in rows)


def test_text_search_owner_name(populated_db_path):
    sql, params = build_parcel_query(
        filters={"owner_name": "LLC"},
        stage=None, limit=1000, offset=0,
    )
    rows = _run(populated_db_path, sql, params)
    # All matching rows contain "LLC" (case-insensitive)
    assert all("LLC" in (r["owner_name"] or "").upper() for r in rows)


def test_column_name_rejects_injection():
    import pytest
    with pytest.raises(ValueError, match="unknown column"):
        build_parcel_query(
            filters={"owner_name; DROP TABLE parcels; --": True},
            stage=None, limit=20, offset=0,
        )


def test_default_query_excludes_condo_units(tmp_path):
    """The list/map default WHERE must hide is_condo_unit=1 rows."""
    from pipeline.db import init_db, get_connection

    db = tmp_path / "t.db"
    init_db(db)
    conn = get_connection(db)
    conn.execute(
        "INSERT INTO parcels (pin, address, is_condo_unit, is_condo_building, "
        "  first_seen_date, last_updated_date, stage) "
        "VALUES ('11111111111111', '1 Visible St', 0, 0, '2026-04-26', '2026-04-26', 'scored')"
    )
    conn.execute(
        "INSERT INTO parcels (pin, address, is_condo_unit, is_condo_building, "
        "  first_seen_date, last_updated_date, stage) "
        "VALUES ('22222222222222', '2 Hidden Unit St', 1, 0, '2026-04-26', '2026-04-26', 'scored')"
    )
    conn.execute(
        "INSERT INTO parcels (pin, address, is_condo_unit, is_condo_building, "
        "  first_seen_date, last_updated_date, stage) "
        "VALUES ('33333333333333', '3 Building St', 0, 1, '2026-04-26', '2026-04-26', 'scored')"
    )
    conn.commit()

    sql, params = build_parcel_query(filters={}, stage=None, limit=10, offset=0)
    rows = conn.execute(sql, params).fetchall()
    pins = {r["pin"] for r in rows}
    assert pins == {"11111111111111", "33333333333333"}


def test_include_condo_units_flag_returns_units(tmp_path):
    """include_condo_units=True must include the hidden unit rows."""
    from pipeline.db import init_db, get_connection

    db = tmp_path / "t.db"
    init_db(db)
    conn = get_connection(db)
    conn.execute(
        "INSERT INTO parcels (pin, address, is_condo_unit, is_condo_building, "
        "  first_seen_date, last_updated_date, stage) "
        "VALUES ('11111111111111', '1 Visible St', 0, 0, '2026-04-26', '2026-04-26', 'scored')"
    )
    conn.execute(
        "INSERT INTO parcels (pin, address, is_condo_unit, is_condo_building, "
        "  first_seen_date, last_updated_date, stage) "
        "VALUES ('22222222222222', '2 Hidden Unit St', 1, 0, '2026-04-26', '2026-04-26', 'scored')"
    )
    conn.commit()

    sql, params = build_parcel_query(
        filters={}, stage=None, limit=10, offset=0, include_condo_units=True
    )
    rows = conn.execute(sql, params).fetchall()
    pins = {r["pin"] for r in rows}
    assert pins == {"11111111111111", "22222222222222"}


def test_sort_by_assessed_total_asc(tmp_path):
    """Explicit sort=assessed_total&dir=asc orders parcels low→high."""
    from pipeline.db import init_db, get_connection

    db = tmp_path / "t.db"
    init_db(db)
    conn = get_connection(db)
    for pin, av in [("11111111111111", 100000),
                    ("22222222222222", 50000),
                    ("33333333333333", 200000)]:
        conn.execute(
            "INSERT INTO parcels (pin, assessed_total, is_condo_unit, "
            "  first_seen_date, last_updated_date, stage) "
            "VALUES (?, ?, 0, '2026-04-26', '2026-04-26', 'scored')",
            (pin, av),
        )
    conn.commit()

    sql, params = build_parcel_query(
        filters={}, stage=None, limit=10, offset=0,
        sort="assessed_total", direction="asc",
    )
    rows = conn.execute(sql, params).fetchall()
    assert [r["pin"] for r in rows] == ["22222222222222", "11111111111111", "33333333333333"]


def test_sort_by_assessed_total_desc(tmp_path):
    from pipeline.db import init_db, get_connection

    db = tmp_path / "t.db"
    init_db(db)
    conn = get_connection(db)
    for pin, av in [("11111111111111", 100000),
                    ("22222222222222", 50000),
                    ("33333333333333", 200000)]:
        conn.execute(
            "INSERT INTO parcels (pin, assessed_total, is_condo_unit, "
            "  first_seen_date, last_updated_date, stage) "
            "VALUES (?, ?, 0, '2026-04-26', '2026-04-26', 'scored')",
            (pin, av),
        )
    conn.commit()

    sql, params = build_parcel_query(
        filters={}, stage=None, limit=10, offset=0,
        sort="assessed_total", direction="desc",
    )
    rows = conn.execute(sql, params).fetchall()
    assert [r["pin"] for r in rows] == ["33333333333333", "11111111111111", "22222222222222"]


def test_sort_invalid_column_raises():
    import pytest
    with pytest.raises(ValueError, match="unknown sort column"):
        build_parcel_query(
            filters={}, stage=None, limit=10, offset=0,
            sort="DROP TABLE parcels", direction="asc",
        )


def test_sort_invalid_direction_raises():
    import pytest
    with pytest.raises(ValueError, match="direction"):
        build_parcel_query(
            filters={}, stage=None, limit=10, offset=0,
            sort="assessed_total", direction="sideways",
        )


def test_sort_none_falls_back_to_default():
    from webapp.parcel_query import DEFAULT_ORDER_BY
    sql, _ = build_parcel_query(filters={}, stage=None, limit=10, offset=0)
    assert DEFAULT_ORDER_BY in sql


def test_count_query_matches_result_query(populated_db_path):
    from webapp.parcel_query import build_count_query
    filters = {"is_absentee": True}
    list_sql, list_params = build_parcel_query(
        filters=filters, stage=None, limit=10000, offset=0
    )
    count_sql, count_params = build_count_query(filters=filters, stage=None)
    n_rows = len(_run(populated_db_path, list_sql, list_params))
    n_count = _run(populated_db_path, count_sql, count_params)[0]["n"]
    expected = _scalar(
        populated_db_path,
        "SELECT COUNT(*) FROM parcels WHERE is_absentee = 1 AND is_condo_unit = 0",
    )
    assert n_rows == n_count == expected
