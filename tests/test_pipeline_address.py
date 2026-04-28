"""Tests for pipeline.address — normalization + range expansion + fuzzy distance."""
from pipeline.address import (
    expand_address_range,
    fuzzy_distance,
    is_absentee,
    is_llc,
    levenshtein,
    split_canonical,
    street_key,
)


def test_street_key_normalizes_suffix_and_direction():
    assert street_key("100 W DIVERSEY PARKWAY") == "100 W DIVERSEY PKWY"
    assert street_key("100 WEST DIVERSEY PKWY") == "100 W DIVERSEY PKWY"
    assert street_key("200 N HALSTED STREET") == "200 N HALSTED ST"
    assert street_key("200 N HALSTED ST") == "200 N HALSTED ST"


def test_street_key_strips_unit_markers():
    assert street_key("100 W DIVERSEY PKWY APT 3") == "100 W DIVERSEY PKWY"
    assert street_key("100 W DIVERSEY PKWY UNIT 3B") == "100 W DIVERSEY PKWY"
    assert street_key("100 W DIVERSEY PKWY #4") == "100 W DIVERSEY PKWY"
    assert street_key("100 W DIVERSEY PKWY FL 2") == "100 W DIVERSEY PKWY"


def test_street_key_normalizes_case_and_whitespace():
    assert street_key("  100 w diversey pkwy  ") == "100 W DIVERSEY PKWY"


def test_street_key_returns_none_on_empty():
    assert street_key(None) is None
    assert street_key("") is None
    assert street_key("   ") is None


def test_expand_address_range_yields_parity_members():
    out = expand_address_range("100-104 W DIVERSEY")
    assert out == ["100 W DIVERSEY", "102 W DIVERSEY", "104 W DIVERSEY"]


def test_expand_address_range_with_suffix():
    out = expand_address_range("100-104 W DIVERSEY PKWY")
    assert out == ["100 W DIVERSEY PKWY", "102 W DIVERSEY PKWY", "104 W DIVERSEY PKWY"]


def test_expand_address_range_passes_through_non_range():
    assert expand_address_range("100 W DIVERSEY") == ["100 W DIVERSEY"]


def test_split_canonical_basic():
    n, d, sfx, mid = split_canonical("100 W DIVERSEY PKWY")
    assert n == "100" and d == "W" and sfx == "PKWY" and mid == ("DIVERSEY",)


def test_split_canonical_multi_word_street():
    n, d, sfx, mid = split_canonical("100 W LAKE SHORE DR")
    assert n == "100" and d == "W" and sfx == "DR" and mid == ("LAKE", "SHORE")


def test_split_canonical_no_suffix():
    n, d, sfx, mid = split_canonical("100 W DIVERSEY")
    assert n == "100" and d == "W" and sfx is None and mid == ("DIVERSEY",)


def test_levenshtein_basic():
    assert levenshtein("kitten", "sitting") == 3
    assert levenshtein("DIVERSEY", "DIVERSY") == 1
    assert levenshtein("", "") == 0
    assert levenshtein("abc", "") == 3


def test_fuzzy_distance_compares_only_middle_tokens():
    a = "100 W DIVERSEY PKWY"
    b = "100 W DIVERSY PKWY"   # missing one E
    assert fuzzy_distance(a, b) == 1


def test_is_llc_detects_common_patterns():
    assert is_llc("RACINE HOLDINGS LLC")
    assert is_llc("Smith Family Trust")
    assert not is_llc("John Smith")
    assert not is_llc(None)


def test_is_absentee_round_trips():
    assert is_absentee("100 W DIVERSEY PKWY", "PO BOX 4421") is True
    assert is_absentee("100 W DIVERSEY PKWY APT 3", "100 W DIVERSEY PKWY") is False
