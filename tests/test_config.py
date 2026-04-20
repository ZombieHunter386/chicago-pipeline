import pytest
from pathlib import Path
from pipeline.config import load_config, get_geography, GeographyConfig


def test_load_geography_returns_polygon_and_bbox(tmp_path):
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "geography.yaml").write_text(
        """
name: Test Area
polygon:
  - [41.0, -87.0]
  - [41.0, -86.0]
  - [40.0, -86.0]
  - [40.0, -87.0]
bbox:
  min_lat: 40.0
  max_lat: 41.0
  min_lng: -87.0
  max_lng: -86.0
"""
    )
    geo = get_geography(cfg_dir)
    assert isinstance(geo, GeographyConfig)
    assert geo.name == "Test Area"
    assert len(geo.polygon) == 4
    assert geo.bbox == (40.0, 41.0, -87.0, -86.0)  # (min_lat, max_lat, min_lng, max_lng)


def test_load_config_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        get_geography(tmp_path / "missing")
