import pytest

from sources.chicago_adu_zones import derive_adu_eligible


@pytest.mark.parametrize("zone_class,in_rs_polygon,expected", [
    # RT/RM/B/C1/C2 are eligible everywhere — polygon flag irrelevant
    ("RT-3.5", False, 1),
    ("RT-3.5", True, 1),
    ("RT-4", False, 1),
    ("RM-5", False, 1),
    ("RM-6.5", True, 1),
    ("B3-2", False, 1),
    ("B1-2", True, 1),
    ("C1-2", False, 1),
    ("C2-3", True, 1),
    # RS zones depend on the polygon containment
    ("RS-1", True, 1),
    ("RS-1", False, 0),
    ("RS-2", True, 1),
    ("RS-2", False, 0),
    ("RS-3", True, 1),
    ("RS-3", False, 0),
    # Not eligible — anywhere
    ("M1-2", False, 0),
    ("M1-2", True, 0),
    ("PD 853", False, 0),
    ("C3-2", False, 0),     # C3+ is NOT in the C1/C2 allowlist
    ("C4-3", True, 0),
    # Edge cases
    (None, False, 0),
    (None, True, 0),
    ("", False, 0),
])
def test_derive_adu_eligible(zone_class, in_rs_polygon, expected):
    assert derive_adu_eligible(zone_class, in_rs_polygon) == expected


def test_derive_adu_eligible_handles_case_insensitive_zone():
    """Real assessor data has mixed casing; the rule should be case-insensitive."""
    assert derive_adu_eligible("rt-3", False) == 1
    assert derive_adu_eligible("Rs-3", True) == 1
    assert derive_adu_eligible("rs-3", False) == 0
