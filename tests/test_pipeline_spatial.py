from pipeline.spatial import match_records_to_parcels


def test_match_returns_index_to_pin_within_radius():
    parcels = [
        {"pin": "A", "lat": 41.93000, "lng": -87.65000},
        {"pin": "B", "lat": 41.93010, "lng": -87.65000},  # ~36ft north of A
    ]
    records = [
        {"latitude": 41.93001, "longitude": -87.65000},   # ~3.6ft from A
        {"latitude": 41.93010, "longitude": -87.65000},   # 0ft from B
        {"latitude": 41.94000, "longitude": -87.65000},   # ~3600ft, no match
    ]
    result = match_records_to_parcels(records, parcels, radius_ft=50.0)
    assert result == {0: "A", 1: "B"}


def test_match_handles_records_with_no_lat_lng():
    parcels = [{"pin": "A", "lat": 41.93, "lng": -87.65}]
    records = [
        {"latitude": None, "longitude": None},
        {"latitude": 41.93, "longitude": -87.65},
    ]
    result = match_records_to_parcels(records, parcels, radius_ft=50.0)
    assert result == {1: "A"}


def test_match_returns_empty_when_no_parcels():
    assert match_records_to_parcels([{"latitude": 41.9, "longitude": -87.6}], [], 50.0) == {}


def test_match_returns_empty_when_no_records():
    assert match_records_to_parcels([], [{"pin": "A", "lat": 41.9, "lng": -87.6}], 50.0) == {}
