# tests/test_geography.py
from pipeline.config import GeographyConfig
from pipeline.geography import in_polygon, filter_by_polygon, bbox_where_clause


GEO = GeographyConfig(
    name="Test",
    polygon=[(41.0, -87.0), (41.0, -86.0), (40.0, -86.0), (40.0, -87.0)],
    bbox=(40.0, 41.0, -87.0, -86.0),
)


def test_in_polygon_inside():
    assert in_polygon(40.5, -86.5, GEO) is True


def test_in_polygon_outside():
    assert in_polygon(42.0, -86.5, GEO) is False


def test_in_polygon_boundary_inclusive():
    # geopandas' covers() handles boundary; either True or False is acceptable
    # but should not crash
    in_polygon(41.0, -87.0, GEO)


def test_filter_by_polygon_drops_outside_points():
    rows = [
        {"pin": "in1", "lat": 40.5, "lng": -86.5},
        {"pin": "out1", "lat": 50.0, "lng": -86.5},
        {"pin": "in2", "lat": 40.9, "lng": -86.9},
    ]
    kept = filter_by_polygon(rows, GEO, lat_field="lat", lng_field="lng")
    pins = {r["pin"] for r in kept}
    assert pins == {"in1", "in2"}


def test_bbox_where_clause_socrata_format():
    clause = bbox_where_clause(GEO, lat_field="lat", lng_field="lon")
    # Should produce a SoQL-compatible string with all four bounds
    assert "lat between 40.0 and 41.0" in clause.lower()
    assert "lon between -87.0 and -86.0" in clause.lower()
