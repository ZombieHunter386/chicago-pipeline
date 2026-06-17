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


def test_score_parcels_multi_clears_stale_scores_before_writing(tmp_path):
    """If a parcel's signals change (or the YAML changes) such that the
    new computed score is different, the stale prior score must not
    persist. _score_one_profile clears the column before writing."""
    from pipeline.db import init_db, get_connection
    from pipeline.score import score_parcels_multi, load_scoring_config

    db = tmp_path / "t.db"
    init_db(db)
    conn = get_connection(db)
    # Seed two parcels with a stale score_adu value the engine should
    # overwrite (or NULL out).
    conn.executemany(
        "INSERT INTO parcels(pin, pin10, lot_size_sf, score_adu) "
        "VALUES (?, ?, ?, ?)",
        [
            ("00000000000001", "0000000000", 4000.0, 99.0),
            ("00000000000002", "0000000000", 10000.0, 88.0),
        ],
    )
    conn.commit()
    conn.close()

    yaml_path = tmp_path / "p.yaml"
    yaml_path.write_text(
        "version: t\n"
        "top_n: 5\n"
        "signals:\n"
        "  lot_size_sf:\n"
        "    weight: 1.0\n"
        "    kind: continuous\n"
        "    direction: positive\n"
        "    insignificant: false\n"
        "    normalization: {min: 1000, max: 12000}\n"
    )
    score_parcels_multi(db, [
        ("adu", load_scoring_config(yaml_path), "score_adu"),
    ])

    conn = get_connection(db)
    # The new scores reflect lot_size_sf normalization (~27.3 and ~81.8),
    # NOT the stale 99/88 values that were seeded.
    for pin in ("00000000000001", "00000000000002"):
        new_score = conn.execute(
            "SELECT score_adu FROM parcels WHERE pin=?", (pin,)
        ).fetchone()[0]
        assert new_score != 99.0, f"pin {pin} retained stale score_adu=99"
        assert new_score != 88.0, f"pin {pin} retained stale score_adu=88"
    conn.close()


def test_score_all_profiles_writes_every_registered_column(tmp_path):
    """score_all_profiles drives the profile_defaults.yaml registry: every
    registered profile re-scores into its score_column. This is the
    re-score-only path for refreshing a DB after a weights edit (no fetch)."""
    from pipeline.score import score_all_profiles

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    # Two profiles writing to two distinct columns via the registry.
    (config_dir / "profile_defaults.yaml").write_text(
        "value_add:\n"
        "  yaml: scoring.yaml\n"
        "  score_column: score\n"
        "adu:\n"
        "  yaml: scoring_adu.yaml\n"
        "  score_column: score_adu\n"
    )
    signal_yaml = (
        "version: t\n"
        "top_n: 5\n"
        "signals:\n"
        "  lot_size_sf:\n"
        "    weight: 1.0\n"
        "    kind: continuous\n"
        "    direction: positive\n"
        "    insignificant: false\n"
        "    normalization: {min: 1000, max: 12000}\n"
    )
    (config_dir / "scoring.yaml").write_text(signal_yaml)
    (config_dir / "scoring_adu.yaml").write_text(signal_yaml)

    db = tmp_path / "t.db"
    init_db(db)
    conn = get_connection(db)
    conn.execute(
        "INSERT INTO parcels(pin, pin10, lot_size_sf) VALUES (?, ?, ?)",
        ("00000000000001", "0000000000", 8000.0),
    )
    conn.commit()
    conn.close()

    counts = score_all_profiles(db, config_dir)
    assert counts == {"value_add": 1, "adu": 1}

    conn = get_connection(db)
    row = conn.execute(
        "SELECT score, score_adu FROM parcels WHERE pin='00000000000001'"
    ).fetchone()
    conn.close()
    assert row["score"] is not None
    assert row["score_adu"] is not None
