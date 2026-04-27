from pathlib import Path

from pipeline.zoning_lookup import load_zoning_lookup, ZoneInfo


PROJECT_LOOKUP = Path(__file__).resolve().parent.parent / "config" / "zoning_lookup.yaml"


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


def test_project_lookup_covers_common_chicago_zones():
    """The shipped zoning_lookup.yaml must cover every common Chicago
    residential / business / commercial zone class so max_far populates
    for as many parcels as possible at full geography."""
    zl = load_zoning_lookup(PROJECT_LOOKUP)
    expected = [
        "RS-1", "RS-2", "RS-3",
        "RT-3.5", "RT-4",
        "RM-4.5", "RM-5", "RM-5.5", "RM-6", "RM-6.5",
        "B1-1", "B1-2", "B1-3", "B1-5",
        "B2-1", "B2-2", "B2-3", "B2-5",
        "B3-1", "B3-2", "B3-3", "B3-5",
        "C1-1", "C1-2", "C1-3", "C1-5",
        "C2-1", "C2-2", "C2-3", "C2-5",
        "C3-1", "C3-2", "C3-3", "C3-5",
        "M1-1", "M1-2", "M1-3",
        "DC", "DX-3", "DX-5", "DX-7", "DX-12", "DX-16",
    ]
    missing = [z for z in expected if z not in zl]
    assert not missing, f"Missing zones in project lookup: {missing}"
    # Every covered zone except parks/transportation must have max_far set.
    for zone in expected:
        info = zl[zone]
        assert info.max_far is not None, f"{zone} has no max_far"
        assert info.max_far > 0, f"{zone} has non-positive max_far"


def test_b_c_zones_follow_dash_digit_far_convention():
    """In B/C zones the digit after the dash maps to FAR: -1 → 1.2,
    -2 → 2.2, -3 → 3.0, -5 → 5.0. Catches typos in the lookup."""
    zl = load_zoning_lookup(PROJECT_LOOKUP)
    expected_far = {"-1": 1.2, "-2": 2.2, "-3": 3.0, "-5": 5.0}
    for prefix in ("B1", "B2", "B3", "C1", "C2", "C3"):
        for suffix, far in expected_far.items():
            zone = prefix + suffix
            if zone in zl:
                assert zl[zone].max_far == far, (
                    f"{zone}: expected max_far={far}, got {zl[zone].max_far}"
                )
