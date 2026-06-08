import json
import math

import responses
from shapely.geometry import Polygon

from sources import (
    assessor_parcels, assessor_characteristics, ccgis_parcels,
)
from pipeline.db import get_connection
from tests.conftest import FIXTURES


def test_polygon_to_width_depth_rectangular():
    """A 25 ft × 125 ft rectangle (standard Chicago lot) returns
    (width=25, depth=125) within 0.5 ft tolerance."""
    from sources.ccgis_parcels import _polygon_to_width_depth
    rect = Polygon([(0, 0), (25, 0), (25, 125), (0, 125)])
    width, depth = _polygon_to_width_depth(rect)
    assert abs(width - 25.0) < 0.5
    assert abs(depth - 125.0) < 0.5


def test_polygon_to_width_depth_irregular_does_not_crash():
    """L-shaped polygon: function returns the minimum rotated rectangle
    dimensions (slightly over-stating the parcel's usable width). Acceptable
    per the spec — we just need it not to crash."""
    from sources.ccgis_parcels import _polygon_to_width_depth
    l_shape = Polygon([(0, 0), (30, 0), (30, 30), (20, 30), (20, 100), (0, 100)])
    width, depth = _polygon_to_width_depth(l_shape)
    assert width > 0
    assert depth > 0
    assert depth >= width  # depth is always the longer side


def test_polygon_to_width_depth_rotated():
    """A rectangle rotated 30 degrees should still report (width, depth)
    correctly — minimum_rotated_rectangle aligns to the polygon, not the axes."""
    from sources.ccgis_parcels import _polygon_to_width_depth
    # 30 ft × 100 ft rectangle, rotated 30°
    angle = math.radians(30)
    cos, sin = math.cos(angle), math.sin(angle)
    corners = [(0, 0), (30, 0), (30, 100), (0, 100)]
    rotated = [(x * cos - y * sin, x * sin + y * cos) for x, y in corners]
    rect = Polygon(rotated)
    width, depth = _polygon_to_width_depth(rect)
    assert abs(width - 30.0) < 0.5
    assert abs(depth - 100.0) < 0.5


def _seed_through_characteristics(db_path, geo, cook_client):
    """Run parcels + characteristics so the DB has pin / pin10 / building_sf
    populated before ccgis_parcels writes lot_size_sf."""
    pf = json.loads((FIXTURES / "assessor_parcels.json").read_text())
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_parcels.DATASET_ID}.json",
        json=pf, status=200)
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_parcels.DATASET_ID}.json",
        json=[], status=200)
    assessor_parcels.fetch(geo, db_path, cook_client)

    cf = json.loads((FIXTURES / "assessor_characteristics.json").read_text())
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_characteristics.DATASET_ID}.json",
        json=cf, status=200)
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_characteristics.DATASET_ID}.json",
        json=[], status=200)
    assessor_characteristics.fetch(geo, db_path, cook_client)


@responses.activate
def test_ccgis_parcels_writes_lot_size_from_polygon_area(db_path, geo, cook_client):
    """The polygon area in EPSG:3435 (US survey feet) becomes lot_size_sf
    for every parcel sharing the polygon's pin10."""
    _seed_through_characteristics(db_path, geo, cook_client)

    fx = json.loads((FIXTURES / "ccgis_parcels.json").read_text())
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{ccgis_parcels.DATASET_ID}.json",
        json=fx, status=200)
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{ccgis_parcels.DATASET_ID}.json",
        json=[], status=200)
    n = ccgis_parcels.fetch(geo, db_path, cook_client)
    assert n == 2

    conn = get_connection(db_path)
    p = conn.execute(
        "SELECT lot_size_sf, building_sf, built_far FROM parcels WHERE pin='14210010010000'"
    ).fetchone()
    # ~0.0001° lng × 0.0001° lat at lat 41.94 ≈ 27 ft × 36 ft ≈ ~1000 sq ft.
    # Sanity: lot is in the ballpark of hundreds → low thousands of sq ft,
    # not zero and not millions.
    assert p["lot_size_sf"] is not None
    assert 500 < p["lot_size_sf"] < 5000
    # built_far recomputed against the new lot.
    assert p["built_far"] is not None
    assert abs(p["built_far"] - p["building_sf"] / p["lot_size_sf"]) < 0.01


@responses.activate
def test_ccgis_parcels_overwrites_existing_lot_size(db_path, geo, cook_client):
    """If a prior run wrote lot_size_sf from another source, ccgis_parcels
    must overwrite it with the polygon-derived value (GIS-first policy)."""
    _seed_through_characteristics(db_path, geo, cook_client)

    # Manually pre-set lot_size_sf to a wrong value.
    conn = get_connection(db_path)
    conn.execute(
        "UPDATE parcels SET lot_size_sf = 99999, built_far = 0.01 WHERE pin='14210010010000'"
    )
    conn.commit()
    conn.close()

    fx = json.loads((FIXTURES / "ccgis_parcels.json").read_text())
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{ccgis_parcels.DATASET_ID}.json",
        json=fx, status=200)
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{ccgis_parcels.DATASET_ID}.json",
        json=[], status=200)
    ccgis_parcels.fetch(geo, db_path, cook_client)

    conn = get_connection(db_path)
    p = conn.execute(
        "SELECT lot_size_sf FROM parcels WHERE pin='14210010010000'"
    ).fetchone()
    assert p["lot_size_sf"] != 99999  # overwritten by GIS area


@responses.activate
def test_ccgis_parcels_writes_lot_width_and_depth(db_path, geo, cook_client):
    """After fetch, parcels.lot_width_ft and parcels.lot_depth_ft are
    populated for every pin sharing a fetched polygon."""
    _seed_through_characteristics(db_path, geo, cook_client)

    fx = json.loads((FIXTURES / "ccgis_parcels.json").read_text())
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{ccgis_parcels.DATASET_ID}.json",
        json=fx, status=200)
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{ccgis_parcels.DATASET_ID}.json",
        json=[], status=200)
    ccgis_parcels.fetch(geo, db_path, cook_client)

    conn = get_connection(db_path)
    rows = conn.execute(
        "SELECT pin, lot_width_ft, lot_depth_ft FROM parcels "
        "WHERE lot_width_ft IS NOT NULL"
    ).fetchall()
    assert len(rows) >= 1, "expected at least one parcel with lot_width_ft populated"
    for r in rows:
        assert r["lot_width_ft"] > 0
        assert r["lot_depth_ft"] >= r["lot_width_ft"]
    conn.close()


@responses.activate
def test_ccgis_parcels_returns_zero_when_no_polygons(db_path, geo, cook_client):
    """Empty Socrata response should return 0 cleanly, not raise."""
    _seed_through_characteristics(db_path, geo, cook_client)
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{ccgis_parcels.DATASET_ID}.json",
        json=[], status=200)
    n = ccgis_parcels.fetch(geo, db_path, cook_client)
    assert n == 0
