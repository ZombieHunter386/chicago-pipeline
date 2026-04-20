from pipeline.zoning_lookup import load_zoning_lookup, ZoneInfo


def test_load_returns_dict_keyed_by_zone_class(tmp_path):
    p = tmp_path / "zoning_lookup.yaml"
    p.write_text(
        """
zones:
  RT-4:
    max_far: 1.2
    max_height_ft: 38
    max_density: null
    min_lot_area_per_unit: 1000
    setback_front_ft: 15
    setback_side_ft: 4
    setback_rear_ft: 30
    allows_multifamily: true
"""
    )
    zl = load_zoning_lookup(p)
    assert "RT-4" in zl
    assert isinstance(zl["RT-4"], ZoneInfo)
    assert zl["RT-4"].max_far == 1.2
    assert zl["RT-4"].allows_multifamily is True
