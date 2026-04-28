"""Tests for pipeline/score.py — applies weights from config/scoring.yaml
to the parcels table to produce a 0-100 score per parcel."""
import yaml

from pipeline import score


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
