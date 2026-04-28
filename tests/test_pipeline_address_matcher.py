"""Tests for pipeline.spatial.match_records_to_parcels_with_address — the
address-first / geo-fallback / fuzzy-review matcher."""
from pipeline.spatial import (
    METHOD_ADDRESS_EXACT,
    METHOD_ADDRESS_RANGE,
    METHOD_GEO,
    match_records_to_parcels_with_address,
)


def _addr(r):
    return r.get("address")


PARCELS = [
    {"pin": "P1", "address": "100 W DIVERSEY PKWY", "lat": 41.94, "lng": -87.65},
    {"pin": "P2", "address": "200 N HALSTED ST",    "lat": 41.93, "lng": -87.66},
    {"pin": "P3", "address": "104 W DIVERSEY PKWY", "lat": 41.94, "lng": -87.6499},
]


def test_address_exact_match():
    records = [{"address": "100 W DIVERSEY PKWY", "latitude": None, "longitude": None}]
    matched, fuzzy = match_records_to_parcels_with_address(
        records, PARCELS, get_record_address=_addr,
    )
    assert matched == {0: ("P1", METHOD_ADDRESS_EXACT)}
    assert fuzzy == []


def test_address_exact_with_unit_suffix():
    records = [{"address": "100 W DIVERSEY PKWY APT 3", "latitude": None, "longitude": None}]
    matched, _ = match_records_to_parcels_with_address(
        records, PARCELS, get_record_address=_addr,
    )
    assert matched == {0: ("P1", METHOD_ADDRESS_EXACT)}


def test_address_full_word_suffix_normalizes():
    records = [{"address": "200 NORTH HALSTED STREET", "latitude": None, "longitude": None}]
    matched, _ = match_records_to_parcels_with_address(
        records, PARCELS, get_record_address=_addr,
    )
    assert matched == {0: ("P2", METHOD_ADDRESS_EXACT)}


def test_address_range_match():
    # "100-104 W DIVERSEY PKWY" should match P1 (100) or P3 (104).
    records = [{"address": "100-104 W DIVERSEY PKWY", "latitude": None, "longitude": None}]
    matched, _ = match_records_to_parcels_with_address(
        records, PARCELS, get_record_address=_addr,
    )
    assert 0 in matched
    pin, method = matched[0]
    assert pin in ("P1", "P3")
    assert method == METHOD_ADDRESS_RANGE


def test_geo_fallback_when_address_misses():
    # No street name match, but lat/lng is right on top of P1.
    records = [{"address": "999 NOWHERE ST", "latitude": 41.94, "longitude": -87.65}]
    matched, _ = match_records_to_parcels_with_address(
        records, PARCELS, get_record_address=_addr, geo_radius_ft=75.0,
    )
    assert 0 in matched
    pin, method = matched[0]
    assert pin == "P1"
    assert method == METHOD_GEO


def test_fuzzy_review_logged_not_auto_matched():
    # Same number+direction, but street name is misspelled by 1 char.
    # No lat/lng so geo fallback can't catch it.
    records = [{"address": "100 W DIVERSY PKWY", "latitude": None, "longitude": None}]
    matched, fuzzy = match_records_to_parcels_with_address(
        records, PARCELS, get_record_address=_addr,
    )
    # Not auto-matched
    assert 0 not in matched
    # Logged for review
    assert any(f["record_index"] == 0 and f["candidate_pin"] == "P1" for f in fuzzy)


def test_no_match_when_far_away_and_unknown_address():
    records = [{"address": "999 NOWHERE ST", "latitude": 30.0, "longitude": -90.0}]
    matched, fuzzy = match_records_to_parcels_with_address(
        records, PARCELS, get_record_address=_addr, geo_radius_ft=75.0,
    )
    assert matched == {}
    assert fuzzy == []


def test_geo_radius_75ft_does_not_pick_neighbor_125ft_away():
    # Place a record 130ft east of P1 (~0.00047 deg lng at lat 41.94).
    # 75ft is well under that, so geo should NOT match.
    records = [{"address": "999 NOWHERE ST", "latitude": 41.94, "longitude": -87.6495}]
    matched, _ = match_records_to_parcels_with_address(
        records, PARCELS, get_record_address=_addr, geo_radius_ft=75.0,
    )
    # The next-nearest parcel is P3 at -87.6499 (~25ft) — so this *will* match P3
    # because P3 is the actual nearest within 75ft. Use a more isolated parcel for
    # the negative test:
    isolated_parcels = [
        {"pin": "PA", "address": "1 SOLO ST", "lat": 41.94, "lng": -87.65},
    ]
    matched2, _ = match_records_to_parcels_with_address(
        records, isolated_parcels, get_record_address=_addr, geo_radius_ft=75.0,
    )
    assert matched2 == {}


def test_drops_record_with_no_address_and_no_latlng():
    records = [{"address": None, "latitude": None, "longitude": None}]
    matched, fuzzy = match_records_to_parcels_with_address(
        records, PARCELS, get_record_address=_addr,
    )
    assert matched == {}
    assert fuzzy == []
