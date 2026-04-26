import sqlite3

import pytest
from webapp.app import create_app


@pytest.fixture
def client(db_path):
    app = create_app(db_path=db_path, feature_outreach=False)
    app.testing = True
    return app.test_client()


def test_index_returns_200_and_html(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.mimetype == "text/html"
    assert b"Chicago Multifamily Pipeline" in resp.data


@pytest.fixture
def pop_client(populated_db_path):
    app = create_app(db_path=populated_db_path, feature_outreach=False)
    app.testing = True
    return app.test_client()


def _scalar(db_path, sql, params=()):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(sql, params).fetchone()[0]
    finally:
        conn.close()


def test_api_filters_returns_schema(pop_client):
    resp = pop_client.get("/api/filters")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "filter_groups" in data
    assert any(g["group"] == "Owner" for g in data["filter_groups"])


def test_api_parcels_default_pagination(pop_client, populated_db_path):
    expected_total = _scalar(populated_db_path, "SELECT COUNT(*) FROM parcels")
    resp = pop_client.get("/api/parcels")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["total"] == expected_total
    assert len(data["parcels"]) == min(20, expected_total)
    assert "pin" in data["parcels"][0]
    assert "address" in data["parcels"][0]


def test_api_parcels_applies_filter(pop_client, populated_db_path):
    expected_total = _scalar(
        populated_db_path,
        "SELECT COUNT(*) FROM parcels WHERE is_absentee = 1",
    )
    resp = pop_client.get("/api/parcels?is_absentee=true&limit=1000")
    data = resp.get_json()
    assert data["total"] == expected_total
    assert all(p["is_absentee"] == 1 for p in data["parcels"])


def test_api_parcels_range_filter(pop_client):
    resp = pop_client.get(
        "/api/parcels?hold_duration_years.min=20&limit=1000"
    )
    data = resp.get_json()
    assert data["total"] > 0
    assert all(p["hold_duration_years"] >= 20 for p in data["parcels"])


def test_api_parcel_detail(pop_client, populated_db_path):
    # Pick a PIN dynamically from the DB
    conn = sqlite3.connect(populated_db_path)
    try:
        pin, address = conn.execute(
            "SELECT pin, address FROM parcels LIMIT 1"
        ).fetchone()
    finally:
        conn.close()

    resp = pop_client.get(f"/api/parcels/{pin}")
    assert resp.status_code == 200
    p = resp.get_json()
    assert p["pin"] == pin
    assert p["address"] == address
    # Google Maps URL is derived server-side
    assert "google_maps_url" in p


def test_api_parcel_detail_404(pop_client):
    resp = pop_client.get("/api/parcels/00000000000000")
    assert resp.status_code == 404


def test_api_map_data_is_geojson(pop_client):
    resp = pop_client.get("/api/map-data")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["type"] == "FeatureCollection"
    assert len(data["features"]) > 0
    feat = data["features"][0]
    assert feat["type"] == "Feature"
    assert feat["geometry"]["type"] == "Point"
    assert "pin" in feat["properties"]
    assert "category" in feat["properties"]
    # Valid categories: top, consolidated, outreach, listed, other
    assert feat["properties"]["category"] in {
        "top", "consolidated", "outreach", "listed", "other"
    }


def test_api_map_data_marks_consolidated(pop_client, populated_db_path):
    # The consolidated count returned by the API should match the count of
    # parcels with consolidation_group_id set in the DB (intersected with
    # parcels that have lat/lng, since features without coords are dropped).
    expected = _scalar(
        populated_db_path,
        """
        SELECT COUNT(*) FROM parcels
        WHERE consolidation_group_id IS NOT NULL
          AND lat IS NOT NULL
          AND lng IS NOT NULL
          AND (listing_status IS NULL OR listing_status != 'listed')
          AND (stage IS NULL OR stage != 'outreach')
        """,
    )
    resp = pop_client.get("/api/map-data")
    data = resp.get_json()
    cats = [f["properties"]["category"] for f in data["features"]]
    assert cats.count("consolidated") == expected


def test_api_parcels_bad_limit_returns_400(pop_client):
    resp = pop_client.get("/api/parcels?limit=abc")
    assert resp.status_code == 400


def test_api_parcels_bad_offset_returns_400(pop_client):
    resp = pop_client.get("/api/parcels?offset=xyz")
    assert resp.status_code == 400


def test_e2e_flow_against_smoke_db(pop_client, populated_db_path):
    """End-to-end smoke flow: schema -> list -> filter -> detail -> map.

    Walks the full UI-driven request sequence and asserts cross-step
    consistency (detail PIN matches list, map PINs are a subset of the
    filtered list, totals agree). Per-route shape/error details live in
    the unit tests above; this test covers the integration.
    """
    # 1. Filter schema is well-formed and has the expected number of groups.
    filters = pop_client.get("/api/filters").get_json()
    assert len(filters["filter_groups"]) == 6

    # 2. First page of the ranked list. Total comes from the DB so the
    #    test isn't pinned to a hardcoded fixture row count.
    expected_total = _scalar(populated_db_path, "SELECT COUNT(*) FROM parcels")
    listing = pop_client.get("/api/parcels?limit=20").get_json()
    assert listing["total"] == expected_total
    assert len(listing["parcels"]) == min(20, expected_total)
    first_pin = listing["parcels"][0]["pin"]

    # 3. Apply a filter and re-load. Consistency: filtered total matches
    #    the DB count, and is a strict subset of the unfiltered total.
    expected_absentee = _scalar(
        populated_db_path,
        "SELECT COUNT(*) FROM parcels WHERE is_absentee = 1",
    )
    filtered = pop_client.get(
        "/api/parcels?is_absentee=true&limit=1000"
    ).get_json()
    assert filtered["total"] == expected_absentee
    assert filtered["total"] <= expected_total
    filtered_pins = {p["pin"] for p in filtered["parcels"]}

    # 4. Detail for the first PIN from the unfiltered list. Confirms the
    #    list -> detail click path: the PIN round-trips and the response
    #    includes the server-derived google_maps_url plus a contacts
    #    array (empty in smoke.db, which has no contacts table rows).
    detail = pop_client.get(f"/api/parcels/{first_pin}").get_json()
    assert detail["pin"] == first_pin
    assert "google_maps_url" in detail
    assert detail["contacts"] == []

    # 5. Map data under the same filter is valid GeoJSON, non-empty, and
    #    bounded above by the filtered list total. Every map PIN must
    #    appear in the filtered list (map is a geo-projection of the
    #    same query, minus rows without lat/lng).
    geo = pop_client.get("/api/map-data?is_absentee=true").get_json()
    assert geo["type"] == "FeatureCollection"
    assert len(geo["features"]) > 0
    assert len(geo["features"]) <= filtered["total"]
    map_pins = {f["properties"]["pin"] for f in geo["features"]}
    assert map_pins.issubset(filtered_pins)
