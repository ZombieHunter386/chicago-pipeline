import sqlite3
from webapp.parcel_query import build_parcel_query


def _run(db_path, sql, params):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
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
    assert len(rows) == 568  # known count from smoke.db
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


def test_count_query_matches_result_query(populated_db_path):
    from webapp.parcel_query import build_count_query
    filters = {"is_absentee": True}
    list_sql, list_params = build_parcel_query(
        filters=filters, stage=None, limit=10000, offset=0
    )
    count_sql, count_params = build_count_query(filters=filters, stage=None)
    n_rows = len(_run(populated_db_path, list_sql, list_params))
    n_count = _run(populated_db_path, count_sql, count_params)[0]["n"]
    assert n_rows == n_count == 568
