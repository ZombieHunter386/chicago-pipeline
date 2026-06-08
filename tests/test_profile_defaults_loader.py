import pytest
from pathlib import Path


def test_load_profile_defaults_returns_registered_profiles(tmp_path):
    from pipeline.profile_defaults import load_profile_defaults

    cfg = tmp_path / "profile_defaults.yaml"
    cfg.write_text("""\
value_add:
  yaml: config/scoring.yaml
  score_column: score
  recommended_filters: {}

adu:
  yaml: config/scoring_adu.yaml
  score_column: score_adu
  recommended_filters:
    adu_eligible: 1
    lot_size_sf: {between: [3500, 12000]}
""")

    out = load_profile_defaults(cfg)
    assert set(out.keys()) == {"value_add", "adu"}
    assert out["adu"]["yaml"] == "config/scoring_adu.yaml"
    assert out["adu"]["score_column"] == "score_adu"
    assert out["adu"]["recommended_filters"]["adu_eligible"] == 1
    assert out["adu"]["recommended_filters"]["lot_size_sf"] == {"between": [3500, 12000]}


def test_load_profile_defaults_raises_on_missing_required_fields(tmp_path):
    from pipeline.profile_defaults import load_profile_defaults

    cfg = tmp_path / "bad.yaml"
    cfg.write_text("adu:\n  recommended_filters: {}\n")
    with pytest.raises(KeyError, match="adu"):
        load_profile_defaults(cfg)
