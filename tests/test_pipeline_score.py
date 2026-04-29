"""Tests for pipeline/score.py — applies weights from config/scoring.yaml
to the parcels table to produce a 0-100 score per parcel."""
from datetime import datetime, UTC
from pathlib import Path

import yaml

from pipeline import score
from pipeline.db import init_db, upsert_rows


def test_module_exposes_expected_public_api():
    """score, normalize_signal, score_parcel, score_parcels, load_scoring_config
    are the public functions. They get filled in across Tasks 2-6."""
    for name in ("score", "normalize_signal", "score_parcel", "score_parcels",
                 "load_scoring_config"):
        assert hasattr(score, name), f"pipeline.score missing {name}"


def test_score_entry_point_signature():
    import inspect
    sig = inspect.signature(score.score)
    assert list(sig.parameters) == ["db_path", "scoring_yaml_path"]


def _write_yaml(path, payload):
    path.write_text(yaml.safe_dump(payload, sort_keys=False))


def test_load_scoring_config_basic_roundtrip(tmp_path):
    yaml_path = tmp_path / "scoring.yaml"
    _write_yaml(yaml_path, {
        "version": "1.0.0-test",
        "generated_at": "2026-04-28T12:00:00+00:00",
        "top_n": 20,
        "signals": {
            "lot_size_sf": {"kind": "continuous", "weight": 0.0,
                            "direction": "positive",
                            "normalization": {"min": 1500.0, "max": 12000.0},
                            "insignificant": True},
            "is_llc":      {"kind": "binary", "weight": 0.153,
                            "direction": "positive",
                            "normalization": {"min": 0.0, "max": 1.0},
                            "insignificant": False},
        },
    })
    cfg = score.load_scoring_config(yaml_path)
    assert cfg.version == "1.0.0-test"
    assert cfg.top_n == 20
    assert len(cfg.signals) == 2
    # Order preserved.
    assert [s.signal for s in cfg.signals] == ["lot_size_sf", "is_llc"]
    lot = cfg.signals[0]
    assert lot.kind == "continuous"
    assert lot.weight == 0.0
    assert lot.normalization_min == 1500.0
    assert lot.normalization_max == 12000.0
    assert lot.insignificant is True
    llc = cfg.signals[1]
    assert llc.weight == 0.153
    assert llc.insignificant is False


def test_load_scoring_config_raises_on_missing_required_field(tmp_path):
    yaml_path = tmp_path / "scoring.yaml"
    _write_yaml(yaml_path, {
        "version": "1.0.0-test",
        "top_n": 20,
        # signals key missing entirely
    })
    import pytest
    with pytest.raises(KeyError, match="signals"):
        score.load_scoring_config(yaml_path)


def _continuous_cfg(min_=0.0, max_=100.0, insignificant=False):
    return score.SignalConfig(
        signal="test", kind="continuous", weight=0.5, direction="positive",
        normalization_min=min_, normalization_max=max_,
        insignificant=insignificant,
    )


def _binary_cfg(insignificant=False):
    return score.SignalConfig(
        signal="test", kind="binary", weight=0.5, direction="positive",
        normalization_min=0.0, normalization_max=1.0,
        insignificant=insignificant,
    )


def test_normalize_continuous_in_range():
    cfg = _continuous_cfg(min_=1500.0, max_=12000.0)
    # Halfway between min and max → 0.5
    assert score.normalize_signal(6750.0, cfg) == 0.5
    # Quarter point
    assert score.normalize_signal(4125.0, cfg) == 0.25


def test_normalize_continuous_clips_above_max():
    cfg = _continuous_cfg(min_=1500.0, max_=12000.0)
    assert score.normalize_signal(50000.0, cfg) == 1.0


def test_normalize_continuous_clips_below_min():
    cfg = _continuous_cfg(min_=1500.0, max_=12000.0)
    assert score.normalize_signal(100.0, cfg) == 0.0


def test_normalize_continuous_null_returns_neutral():
    cfg = _continuous_cfg(min_=1500.0, max_=12000.0)
    assert score.normalize_signal(None, cfg) == 0.5


def test_normalize_continuous_degenerate_range_returns_neutral():
    """When min == max (signal was 100% imputed during Analyze), return 0.5
    instead of dividing by zero. These signals are insignificant anyway, so
    contribution will be zero — but normalize must not crash."""
    cfg = _continuous_cfg(min_=7.62, max_=7.62, insignificant=True)
    assert score.normalize_signal(7.62, cfg) == 0.5
    assert score.normalize_signal(0.0, cfg) == 0.5
    assert score.normalize_signal(None, cfg) == 0.5


def test_normalize_binary_value():
    cfg = _binary_cfg()
    assert score.normalize_signal(1, cfg) == 1.0
    assert score.normalize_signal(0, cfg) == 0.0


def test_normalize_binary_null_returns_zero():
    """Binary NULL means 'not flagged' — contributes 0, not 0.5 (which
    would inflate the score for unflagged parcels)."""
    cfg = _binary_cfg()
    assert score.normalize_signal(None, cfg) == 0.0


def _config(signals):
    return score.ScoringConfig(version="1.0.0-test", top_n=20, signals=signals)


def test_score_parcel_positive_direction_in_range():
    """A parcel halfway through the lot_size range with weight 1.0
    (only signal) should score 50."""
    cfg = _config([
        score.SignalConfig(signal="lot_size_sf", kind="continuous", weight=1.0,
                           direction="positive",
                           normalization_min=1000.0, normalization_max=11000.0,
                           insignificant=False),
    ])
    parcel = {"lot_size_sf": 6000.0}  # exactly the midpoint
    assert score.score_parcel(parcel, cfg) == 50.0


def test_score_parcel_negative_direction_flips():
    """Negative-direction signal at the high end of its range should
    contribute LESS to the score than a parcel at the low end."""
    cfg = _config([
        score.SignalConfig(signal="estimated_annual_tax", kind="continuous",
                           weight=1.0, direction="negative",
                           normalization_min=1000.0, normalization_max=11000.0,
                           insignificant=False),
    ])
    high_tax = {"estimated_annual_tax": 11000.0}   # normalized = 1.0
    low_tax  = {"estimated_annual_tax": 1000.0}    # normalized = 0.0
    # High tax → flipped to 0.0 → contribution 0.0 → score 0
    assert score.score_parcel(high_tax, cfg) == 0.0
    # Low tax → flipped to 1.0 → contribution 1.0 → score 100
    assert score.score_parcel(low_tax, cfg) == 100.0


def test_score_parcel_combines_weighted_signals():
    """Weights sum to 1.0 across signals; per-signal contribution is in
    [0, weight]; final score is in [0, 100]."""
    cfg = _config([
        score.SignalConfig(signal="lot_size_sf", kind="continuous", weight=0.6,
                           direction="positive",
                           normalization_min=0.0, normalization_max=10000.0,
                           insignificant=False),
        score.SignalConfig(signal="is_llc", kind="binary", weight=0.4,
                           direction="positive",
                           normalization_min=0.0, normalization_max=1.0,
                           insignificant=False),
    ])
    # Parcel at 75% of lot range (normalized 0.75) and is_llc=1
    # contributions: 0.75 * 0.6 + 1.0 * 0.4 = 0.45 + 0.40 = 0.85 → 85
    parcel = {"lot_size_sf": 7500.0, "is_llc": 1}
    assert score.score_parcel(parcel, cfg) == 85.0


def test_score_parcel_insignificant_signal_contributes_zero():
    """Even with a non-zero raw value, weight=0 means contribution=0."""
    cfg = _config([
        score.SignalConfig(signal="lot_size_sf", kind="continuous", weight=1.0,
                           direction="positive",
                           normalization_min=0.0, normalization_max=10000.0,
                           insignificant=False),
        score.SignalConfig(signal="years_since_last_permit", kind="continuous",
                           weight=0.0, direction="positive",
                           normalization_min=7.62, normalization_max=7.62,
                           insignificant=True),
    ])
    parcel = {"lot_size_sf": 5000.0, "years_since_last_permit": 999.0}
    # Only lot_size contributes: 0.5 * 1.0 = 0.5 → 50
    assert score.score_parcel(parcel, cfg) == 50.0


def test_score_parcel_handles_null_columns():
    """Missing signal in parcel_row treated as NULL → 0.5 for continuous,
    0 for binary."""
    cfg = _config([
        score.SignalConfig(signal="lot_size_sf", kind="continuous", weight=0.5,
                           direction="positive",
                           normalization_min=0.0, normalization_max=10000.0,
                           insignificant=False),
        score.SignalConfig(signal="is_llc", kind="binary", weight=0.5,
                           direction="positive",
                           normalization_min=0.0, normalization_max=1.0,
                           insignificant=False),
    ])
    parcel = {"lot_size_sf": None, "is_llc": None}
    # lot_size NULL → 0.5; is_llc NULL → 0.0
    # contributions: 0.5 * 0.5 + 0.0 * 0.5 = 0.25 → 25
    assert score.score_parcel(parcel, cfg) == 25.0


def test_score_parcel_clamps_to_zero_to_hundred():
    """Sanity check: every realistic input lands in [0, 100]."""
    cfg = _config([
        score.SignalConfig(signal="x", kind="continuous", weight=1.0,
                           direction="positive",
                           normalization_min=0.0, normalization_max=100.0,
                           insignificant=False),
    ])
    assert score.score_parcel({"x": -5000}, cfg) == 0.0
    assert score.score_parcel({"x": 5000}, cfg) == 100.0


def _build_score_db(tmp_path, parcels):
    db_path = tmp_path / "score.db"
    init_db(db_path)
    now = datetime.now(UTC).isoformat(timespec="seconds")
    upsert_rows(db_path, "parcels",
                [{**p, "last_fetched_date": now} for p in parcels],
                key_columns=["pin"])
    return db_path


def test_score_parcels_updates_score_and_version(tmp_path):
    parcels = [
        {"pin": "14210010010000", "lot_size_sf": 5000.0, "is_llc": 1},
        {"pin": "14210010020000", "lot_size_sf": 9000.0, "is_llc": 0},
    ]
    db_path = _build_score_db(tmp_path, parcels)
    cfg = _config([
        score.SignalConfig(signal="lot_size_sf", kind="continuous", weight=0.5,
                           direction="positive",
                           normalization_min=0.0, normalization_max=10000.0,
                           insignificant=False),
        score.SignalConfig(signal="is_llc", kind="binary", weight=0.5,
                           direction="positive",
                           normalization_min=0.0, normalization_max=1.0,
                           insignificant=False),
    ])
    cfg = score.ScoringConfig(version="1.0.0-test", top_n=20, signals=cfg.signals)
    n = score.score_parcels(db_path, cfg)
    assert n == 2

    from pipeline.db import get_connection
    conn = get_connection(db_path)
    try:
        rows = {r["pin"]: dict(r) for r in conn.execute(
            "SELECT pin, score, score_version FROM parcels"
        ).fetchall()}
    finally:
        conn.close()

    # Parcel 1: lot=0.5*0.5 + is_llc=1.0*0.5 = 0.75 → 75
    assert rows["14210010010000"]["score"] == 75.0
    assert rows["14210010010000"]["score_version"] == "1.0.0-test"
    # Parcel 2: lot=0.9*0.5 + is_llc=0.0*0.5 = 0.45 → 45
    assert rows["14210010020000"]["score"] == 45.0
    assert rows["14210010020000"]["score_version"] == "1.0.0-test"


def test_score_parcels_is_idempotent(tmp_path):
    """Running score_parcels twice with the same DB + config produces identical
    score values — Score is deterministic, no per-run randomness."""
    parcels = [{"pin": "14210010010000", "lot_size_sf": 5000.0, "is_llc": 1}]
    db_path = _build_score_db(tmp_path, parcels)
    cfg = score.ScoringConfig(
        version="1.0.0-test", top_n=20,
        signals=[score.SignalConfig(signal="lot_size_sf", kind="continuous",
                                    weight=1.0, direction="positive",
                                    normalization_min=0.0, normalization_max=10000.0,
                                    insignificant=False)],
    )
    score.score_parcels(db_path, cfg)
    from pipeline.db import get_connection
    conn = get_connection(db_path)
    try:
        first = conn.execute(
            "SELECT score FROM parcels WHERE pin='14210010010000'"
        ).fetchone()["score"]
    finally:
        conn.close()

    score.score_parcels(db_path, cfg)
    conn = get_connection(db_path)
    try:
        second = conn.execute(
            "SELECT score FROM parcels WHERE pin='14210010010000'"
        ).fetchone()["score"]
    finally:
        conn.close()

    assert first == second


def test_score_parcels_handles_empty_db(tmp_path):
    """No parcels → returns 0, no-op."""
    db_path = tmp_path / "empty.db"
    init_db(db_path)
    cfg = score.ScoringConfig(version="1.0.0-test", top_n=20, signals=[])
    assert score.score_parcels(db_path, cfg) == 0


def test_score_orchestrator_writes_scores(tmp_path):
    parcels = [{"pin": "14210010010000", "lot_size_sf": 5000.0}]
    db_path = _build_score_db(tmp_path, parcels)
    yaml_path = tmp_path / "scoring.yaml"
    _write_yaml(yaml_path, {
        "version": "1.0.0-test",
        "generated_at": "2026-04-28T12:00:00+00:00",
        "top_n": 20,
        "signals": {
            "lot_size_sf": {"kind": "continuous", "weight": 1.0,
                            "direction": "positive",
                            "normalization": {"min": 0.0, "max": 10000.0},
                            "insignificant": False},
        },
    })
    score.score(db_path=db_path, scoring_yaml_path=yaml_path)

    from pipeline.db import get_connection
    conn = get_connection(db_path)
    try:
        row = dict(conn.execute(
            "SELECT score, score_version FROM parcels WHERE pin='14210010010000'"
        ).fetchone())
    finally:
        conn.close()
    # 5000 / 10000 * 100 = 50
    assert row["score"] == 50.0
    assert row["score_version"] == "1.0.0-test"


def test_cli_runs_score_against_synthetic_db(tmp_path):
    import subprocess, sys
    parcels = [{"pin": "14210010010000", "lot_size_sf": 5000.0}]
    db_path = _build_score_db(tmp_path, parcels)
    yaml_path = tmp_path / "scoring.yaml"
    _write_yaml(yaml_path, {
        "version": "1.0.0-cli-test",
        "generated_at": "2026-04-28T12:00:00+00:00",
        "top_n": 20,
        "signals": {
            "lot_size_sf": {"kind": "continuous", "weight": 1.0,
                            "direction": "positive",
                            "normalization": {"min": 0.0, "max": 10000.0},
                            "insignificant": False},
        },
    })
    result = subprocess.run([
        sys.executable, "-m", "pipeline.score",
        "--db", str(db_path),
        "--scoring-yaml", str(yaml_path),
    ], capture_output=True, text=True,
       cwd=str(Path(__file__).resolve().parent.parent))
    assert result.returncode == 0, f"stderr: {result.stderr}"
    from pipeline.db import get_connection
    conn = get_connection(db_path)
    try:
        version = conn.execute(
            "SELECT score_version FROM parcels WHERE pin='14210010010000'"
        ).fetchone()["score_version"]
    finally:
        conn.close()
    assert version == "1.0.0-cli-test"


def test_score_parcels_skips_individual_condo_units(tmp_path):
    """Individual condo units (is_condo_unit=1) must NOT receive a score —
    they were dropped from training and are not redevelopment opportunities.
    Building reps (is_condo_building=1) MUST receive a score."""
    parcels = [
        {"pin": "REGULAR", "lot_size_sf": 5000.0, "is_condo_unit": 0,
         "is_condo_building": 0},
        {"pin": "BUILDING_REP", "lot_size_sf": 40000.0, "is_condo_unit": 0,
         "is_condo_building": 1},
        {"pin": "UNIT_1", "lot_size_sf": 40000.0, "is_condo_unit": 1,
         "is_condo_building": 0},
        {"pin": "UNIT_2", "lot_size_sf": 40000.0, "is_condo_unit": 1,
         "is_condo_building": 0},
    ]
    db_path = _build_score_db(tmp_path, parcels)
    cfg = score.ScoringConfig(version="1.0.0-test", top_n=20, signals=[
        score.SignalConfig(signal="lot_size_sf", kind="continuous",
                           weight=1.0, direction="positive",
                           normalization_min=0.0, normalization_max=10000.0,
                           insignificant=False),
    ])
    n = score.score_parcels(db_path, cfg)
    # Only the regular parcel and the building rep are scored — 2, not 4.
    assert n == 2

    from pipeline.db import get_connection
    conn = get_connection(db_path)
    try:
        rows = {r["pin"]: r["score"] for r in conn.execute(
            "SELECT pin, score FROM parcels"
        ).fetchall()}
    finally:
        conn.close()

    assert rows["REGULAR"] is not None
    assert rows["BUILDING_REP"] is not None
    assert rows["UNIT_1"] is None
    assert rows["UNIT_2"] is None


def test_score_parcels_clears_stale_scores_from_filtered_rows(tmp_path):
    """Methodology change: a parcel that WAS scored under a prior methodology
    but is now excluded by the eligibility filter (is_condo_unit=1) must have
    its stale score CLEARED. Without this, downstream consumers can read old
    scores tagged with a stale score_version."""
    parcels = [
        {"pin": "REGULAR", "lot_size_sf": 5000.0, "is_condo_unit": 0},
        # Simulate a condo unit that received a score under the old methodology
        # (pre-1.1.0, which didn't filter condo units).
        {"pin": "STALE_CONDO", "lot_size_sf": 40000.0, "is_condo_unit": 1,
         "score": 95.0, "score_version": "stale-old-version"},
    ]
    db_path = _build_score_db(tmp_path, parcels)
    cfg = score.ScoringConfig(version="1.1.0-test", top_n=20, signals=[
        score.SignalConfig(signal="lot_size_sf", kind="continuous",
                           weight=1.0, direction="positive",
                           normalization_min=0.0, normalization_max=10000.0,
                           insignificant=False),
    ])
    score.score_parcels(db_path, cfg)
    from pipeline.db import get_connection
    conn = get_connection(db_path)
    try:
        rows = {r["pin"]: dict(r) for r in conn.execute(
            "SELECT pin, score, score_version FROM parcels"
        ).fetchall()}
    finally:
        conn.close()
    # REGULAR gets the new score with the new version
    assert rows["REGULAR"]["score"] is not None
    assert rows["REGULAR"]["score_version"] == "1.1.0-test"
    # STALE_CONDO must have its old score AND old version CLEARED, not
    # silently retained from the prior methodology.
    assert rows["STALE_CONDO"]["score"] is None
    assert rows["STALE_CONDO"]["score_version"] is None


import json as _json


def test_score_consolidation_groups_writes_score_per_group(tmp_path):
    """A 2-parcel consolidation group is scored using aggregated features.
    The result is written to consolidation_groups.score / .score_version."""
    parcels = [
        {"pin": "PIN_A", "lot_size_sf": 3000.0, "estimated_annual_tax": 8000.0,
         "is_condo_unit": 0},
        {"pin": "PIN_B", "lot_size_sf": 4000.0, "estimated_annual_tax": 12000.0,
         "is_condo_unit": 0},
    ]
    db_path = _build_score_db(tmp_path, parcels)
    # Add a consolidation group manually
    from pipeline.db import get_connection
    conn = get_connection(db_path)
    try:
        conn.execute("""
            INSERT INTO consolidation_groups
              (group_id, pins, combined_lot_size_sf, combined_building_sf,
               owner_name, detected_date)
            VALUES (1, ?, 7000.0, NULL, 'TEST OWNER', '2026-04-28')
        """, (_json.dumps(["PIN_A", "PIN_B"]),))
        conn.commit()
    finally:
        conn.close()

    cfg = score.ScoringConfig(version="1.1.0-test", top_n=20, signals=[
        score.SignalConfig(signal="lot_size_sf", kind="continuous",
                           weight=0.5, direction="positive",
                           normalization_min=0.0, normalization_max=10000.0,
                           insignificant=False),
        score.SignalConfig(signal="estimated_annual_tax", kind="continuous",
                           weight=0.5, direction="negative",
                           normalization_min=0.0, normalization_max=20000.0,
                           insignificant=False),
    ])
    n = score.score_consolidation_groups(db_path, cfg)
    assert n == 1

    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT score, score_version FROM consolidation_groups WHERE group_id = 1"
        ).fetchone()
    finally:
        conn.close()
    # combined_lot_size_sf=7000 / 10000 = 0.7 → contribution 0.7 * 0.5 = 0.35
    # SUM(tax)=20000, normalized = 1.0, direction negative → flipped to 0.0
    #   → contribution 0.0 * 0.5 = 0.0
    # total = 0.35 → score 35.0
    assert row["score"] == 35.0
    assert row["score_version"] == "1.1.0-test"


def test_score_consolidation_groups_handles_empty_table(tmp_path):
    db_path = _build_score_db(tmp_path, [])
    cfg = score.ScoringConfig(version="1.1.0-test", top_n=20, signals=[])
    assert score.score_consolidation_groups(db_path, cfg) == 0


def test_score_orchestrator_writes_both_parcels_and_groups(tmp_path):
    parcels = [
        {"pin": "PIN_A", "lot_size_sf": 3000.0, "is_condo_unit": 0},
        {"pin": "PIN_B", "lot_size_sf": 4000.0, "is_condo_unit": 0},
    ]
    db_path = _build_score_db(tmp_path, parcels)
    from pipeline.db import get_connection
    conn = get_connection(db_path)
    try:
        conn.execute("""
            INSERT INTO consolidation_groups
              (group_id, pins, combined_lot_size_sf, combined_building_sf,
               owner_name, detected_date)
            VALUES (1, ?, 7000.0, NULL, 'TEST OWNER', '2026-04-28')
        """, (_json.dumps(["PIN_A", "PIN_B"]),))
        conn.commit()
    finally:
        conn.close()

    yaml_path = tmp_path / "scoring.yaml"
    _write_yaml(yaml_path, {
        "version": "1.1.0-test",
        "generated_at": "2026-04-28T12:00:00+00:00",
        "top_n": 20,
        "signals": {
            "lot_size_sf": {"kind": "continuous", "weight": 1.0,
                            "direction": "positive",
                            "normalization": {"min": 0.0, "max": 10000.0},
                            "insignificant": False},
        },
    })
    score.score(db_path=db_path, scoring_yaml_path=yaml_path)

    conn = get_connection(db_path)
    try:
        n_parcels_scored = conn.execute(
            "SELECT COUNT(*) FROM parcels WHERE score IS NOT NULL"
        ).fetchone()[0]
        n_groups_scored = conn.execute(
            "SELECT COUNT(*) FROM consolidation_groups WHERE score IS NOT NULL"
        ).fetchone()[0]
    finally:
        conn.close()
    assert n_parcels_scored == 2
    assert n_groups_scored == 1
