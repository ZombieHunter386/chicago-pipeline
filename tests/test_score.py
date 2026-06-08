"""Tests for pipeline.score — scoring engine + derived signal helpers."""
from __future__ import annotations
import pytest
from pathlib import Path

from pipeline.db import init_db, get_connection


def test_derive_last_sale_price_recent_only_when_recent(tmp_path):
    """last_sale_price_recent mirrors last_sale_price WHEN
    hold_duration_years <= 3, else NULL."""
    from pipeline.score import derive_last_sale_price_recent

    db = tmp_path / "t.db"
    init_db(db)
    conn = get_connection(db)
    conn.executemany(
        "INSERT INTO parcels(pin, last_sale_price, hold_duration_years) "
        "VALUES (?, ?, ?)",
        [
            ("00000000000001", 500000.0, 1.5),
            ("00000000000002", 900000.0, 3.0),
            ("00000000000003", 250000.0, 3.01),
            ("00000000000004", 800000.0, 10.0),
            ("00000000000005", None, 1.0),
            ("00000000000006", 700000.0, None),
        ],
    )
    conn.commit()
    conn.close()

    derive_last_sale_price_recent(db)

    conn = get_connection(db)
    rows = {r["pin"]: r["last_sale_price_recent"] for r in conn.execute(
        "SELECT pin, last_sale_price_recent FROM parcels ORDER BY pin"
    )}
    assert rows["00000000000001"] == 500000.0
    assert rows["00000000000002"] == 900000.0
    assert rows["00000000000003"] is None
    assert rows["00000000000004"] is None
    assert rows["00000000000005"] is None
    assert rows["00000000000006"] is None
    conn.close()


def test_score_parcels_multi_profile_writes_separate_columns(tmp_path):
    """Two profiles in one engine pass write to two distinct columns.
    Single-profile case stays backward-compatible (legacy `score` column)."""
    from pipeline.score import score_parcels_multi, load_scoring_config

    db = tmp_path / "t.db"
    init_db(db)
    conn = get_connection(db)
    conn.executemany(
        "INSERT INTO parcels(pin, pin10, lot_size_sf, is_absentee) "
        "VALUES (?, ?, ?, ?)",
        [
            ("00000000000001", "0000000000", 4000.0, 1),
            ("00000000000002", "0000000000", 10000.0, 0),
        ],
    )
    conn.commit()
    conn.close()

    yaml_a = tmp_path / "profile_a.yaml"
    yaml_a.write_text(
        "version: t-a\n"
        "top_n: 5\n"
        "signals:\n"
        "  lot_size_sf:\n"
        "    weight: 1.0\n"
        "    kind: continuous\n"
        "    direction: positive\n"
        "    insignificant: false\n"
        "    normalization: {min: 1000, max: 12000}\n"
    )
    yaml_b = tmp_path / "profile_b.yaml"
    yaml_b.write_text(
        "version: t-b\n"
        "top_n: 5\n"
        "signals:\n"
        "  is_absentee:\n"
        "    weight: 1.0\n"
        "    kind: binary\n"
        "    direction: positive\n"
        "    insignificant: false\n"
        "    normalization: {min: 0, max: 1}\n"
    )

    score_parcels_multi(db, [
        ("a", load_scoring_config(yaml_a), "score_adu"),
        ("b", load_scoring_config(yaml_b), "score_redev"),
    ])

    conn = get_connection(db)
    row1 = conn.execute(
        "SELECT score_adu, score_redev FROM parcels WHERE pin='00000000000001'"
    ).fetchone()
    row2 = conn.execute(
        "SELECT score_adu, score_redev FROM parcels WHERE pin='00000000000002'"
    ).fetchone()
    assert row2["score_adu"] > row1["score_adu"]
    assert row1["score_redev"] > row2["score_redev"]
    conn.close()
