"""Tests for pipeline/score.py — applies weights from config/scoring.yaml
to the parcels table to produce a 0-100 score per parcel."""
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
