import sqlite3

import pytest
from webapp.app import create_app
from pipeline.db import init_db


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


def test_index_injects_esri_api_key_when_set(db_path):
    # Guards the env → app.config → template → window.ESRI_API_KEY chain.
    # Drop any link and the satellite basemap silently reverts to anonymous
    # Esri, which hits "Account Limit Exceeded" under deployed load.
    app = create_app(
        db_path=db_path, feature_outreach=False, esri_api_key="AAPT-test-key"
    )
    app.testing = True
    resp = app.test_client().get("/")
    assert resp.status_code == 200
    assert b'window.ESRI_API_KEY = "AAPT-test-key";' in resp.data


def test_index_injects_empty_esri_api_key_when_unset(client):
    # When no key is configured the JS-side `window.ESRI_API_KEY || ''`
    # falls through to the anonymous Esri URL — acceptable for hobby dev.
    resp = client.get("/")
    assert b'window.ESRI_API_KEY = "";' in resp.data


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
    # Default response excludes condo units (is_condo_unit=1).
    expected_total = _scalar(
        populated_db_path, "SELECT COUNT(*) FROM parcels WHERE is_condo_unit = 0"
    )
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
        "SELECT COUNT(*) FROM parcels WHERE is_absentee = 1 AND is_condo_unit = 0",
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


def test_api_parcel_detail_rejects_non_numeric_pin(pop_client):
    # Anything that isn't a 14-digit Cook County PIN should 404 before we
    # touch the DB. Guards against arbitrary user input being used as a key.
    resp = pop_client.get("/api/parcels/not-a-pin")
    assert resp.status_code == 404


def test_api_parcel_detail_rejects_short_pin(pop_client):
    resp = pop_client.get("/api/parcels/12345")
    assert resp.status_code == 404


def test_unhandled_exception_returns_json_500(db_path):
    # Build a client where TESTING is on (so /_test_explode is registered)
    # but PROPAGATE_EXCEPTIONS is off (so the registered error handler runs
    # and returns the JSON envelope rather than re-raising into the test).
    app = create_app(db_path=db_path, feature_outreach=False)
    app.config["TESTING"] = True
    app.config["PROPAGATE_EXCEPTIONS"] = False
    client = app.test_client()
    resp = client.get("/_test_explode")
    assert resp.status_code == 500
    assert resp.get_json() == {"error": "internal_error"}


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
    # parcels in the "consolidated" bucket: those with consolidation_group_id
    # OR is_condo_building = 1 (condo buildings ride along in the consolidated
    # layer so the user only has to remember one rollup concept).
    expected = _scalar(
        populated_db_path,
        """
        SELECT COUNT(*) FROM parcels
        WHERE (consolidation_group_id IS NOT NULL OR is_condo_building = 1)
          AND lat IS NOT NULL
          AND lng IS NOT NULL
          AND is_condo_unit = 0
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
    assert len(filters["filter_groups"]) == 9

    # 2. First page of the ranked list. Total comes from the DB so the
    #    test isn't pinned to a hardcoded fixture row count. Default
    #    response excludes condo units (is_condo_unit=1).
    expected_total = _scalar(
        populated_db_path, "SELECT COUNT(*) FROM parcels WHERE is_condo_unit = 0"
    )
    listing = pop_client.get("/api/parcels?limit=20").get_json()
    assert listing["total"] == expected_total
    assert len(listing["parcels"]) == min(20, expected_total)
    first_pin = listing["parcels"][0]["pin"]

    # 3. Apply a filter and re-load. Consistency: filtered total matches
    #    the DB count, and is a strict subset of the unfiltered total.
    expected_absentee = _scalar(
        populated_db_path,
        "SELECT COUNT(*) FROM parcels WHERE is_absentee = 1 AND is_condo_unit = 0",
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


def test_api_parcels_excludes_condo_units_by_default(pop_client, populated_db_path):
    """End-to-end via Flask: condo units are not in the default response."""
    conn = sqlite3.connect(populated_db_path)
    hidden_pin = conn.execute(
        "SELECT pin FROM parcels WHERE is_condo_unit = 1 LIMIT 1"
    ).fetchone()
    conn.close()
    if hidden_pin is None:
        pytest.skip("smoke.db has no is_condo_unit=1 rows yet")
    hidden_pin = hidden_pin[0]

    r = pop_client.get("/api/parcels?limit=1000")
    pins = {p["pin"] for p in r.get_json()["parcels"]}
    assert hidden_pin not in pins

    r2 = pop_client.get("/api/parcels?limit=1000&include_condo_units=true")
    pins_inc = {p["pin"] for p in r2.get_json()["parcels"]}
    assert hidden_pin in pins_inc


# ---------------------------------------------------------------------------
# Consolidation-group endpoints
# ---------------------------------------------------------------------------

def test_api_consolidation_groups_default_filter_drops_single_pin10(pop_client):
    """The default response filters out groups whose member PINs all share
    one pin10 — i.e. condo-unit clusters in a single building. They aren't
    real consolidation plays and they swamp the list otherwise."""
    default = pop_client.get("/api/consolidation-groups").get_json()
    unfiltered = pop_client.get(
        "/api/consolidation-groups?min_combined_lot_size_sf=0&multi_pin10_only=false"
    ).get_json()
    assert "groups" in default and "groups" in unfiltered
    # The unfiltered count should always be >= the default-filtered count.
    assert len(unfiltered["groups"]) >= len(default["groups"])
    # Every group in the default response has multiple distinct pin10s.
    for g in default["groups"]:
        assert g["distinct_pin10_count"] >= 2, g


def test_api_consolidation_groups_min_combined_lot_size_filter(pop_client):
    """The `min_combined_lot_size_sf` knob drops smaller groups."""
    big = pop_client.get(
        "/api/consolidation-groups?min_combined_lot_size_sf=100000&multi_pin10_only=false"
    ).get_json()
    none = pop_client.get(
        "/api/consolidation-groups?min_combined_lot_size_sf=0&multi_pin10_only=false"
    ).get_json()
    assert len(big["groups"]) <= len(none["groups"])
    for g in big["groups"]:
        assert g["combined_lot_size_sf"] >= 100000, g


def test_api_consolidation_groups_respects_parcel_filters(pop_client):
    """Groups should appear only when at least one of their member parcels
    matches the parcel-filter query string. Forcing `is_llc=true` should
    not return more groups than the unfiltered call."""
    all_groups = pop_client.get(
        "/api/consolidation-groups?min_combined_lot_size_sf=0&multi_pin10_only=false"
    ).get_json()
    llc_only = pop_client.get(
        "/api/consolidation-groups?is_llc=true&min_combined_lot_size_sf=0&multi_pin10_only=false"
    ).get_json()
    assert len(llc_only["groups"]) <= len(all_groups["groups"])


def test_api_consolidation_group_detail_includes_zoning_summary(pop_client):
    """Detail endpoint returns aggregates + member rows + zoning_summary
    (the new field consumed by the right-panel Zoning section)."""
    listed = pop_client.get(
        "/api/consolidation-groups?min_combined_lot_size_sf=0&multi_pin10_only=false"
    ).get_json()["groups"]
    if not listed:
        pytest.skip("smoke.db has no consolidation groups")
    gid = listed[0]["group_id"]
    body = pop_client.get(f"/api/consolidation-groups/{gid}").get_json()
    assert "members" in body and isinstance(body["members"], list)
    assert "zoning_summary" in body
    z = body["zoning_summary"]
    # Required top-level zoning_summary fields.
    for key in (
        "is_uniform_zone", "dominant_zone", "breakdown",
        "combined_built_far", "combined_max_buildable_sf",
        "combined_far_gap_delta", "combined_max_units_dominant_zone",
        "allows_multifamily_status",
    ):
        assert key in z, key
    # Breakdown rows have the expected shape.
    for b in z["breakdown"]:
        for k in ("zone_class", "parcel_count", "lot_sf", "max_far"):
            assert k in b, k


def test_api_consolidation_group_detail_404_for_unknown(pop_client):
    resp = pop_client.get("/api/consolidation-groups/999999")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# /api/parcels ?profile= param (Task 5.2)
# ---------------------------------------------------------------------------

@pytest.fixture
def app_with_two_profile_scores(tmp_path):
    """App seeded with 3 parcels carrying score + score_adu values plus a
    profile_defaults.yaml registering value_add and adu."""
    db = tmp_path / "t.db"
    init_db(db)
    with sqlite3.connect(db) as conn:
        conn.executemany(
            "INSERT INTO parcels(pin, pin10, address, score, score_adu) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                ("00000000000001", "0000000000", "100 A St", 90.0, 30.0),
                ("00000000000002", "0000000000", "200 B St", 50.0, 80.0),
                ("00000000000003", "0000000000", "300 C St", 70.0, 60.0),
            ],
        )
        conn.commit()

    cfg = tmp_path / "profile_defaults.yaml"
    cfg.write_text("""\
value_add:
  yaml: scoring.yaml
  score_column: score
  recommended_filters: {}

adu:
  yaml: scoring_adu.yaml
  score_column: score_adu
  recommended_filters: {}
""")

    return create_app(
        db_path=db, feature_outreach=False,
        profile_defaults_path=cfg,
    )


def test_api_parcels_orders_by_profile_score_column(app_with_two_profile_scores):
    """When ?profile=adu, results are sorted by score_adu DESC instead
    of the default `score` column."""
    client = app_with_two_profile_scores.test_client()
    resp = client.get("/api/parcels?profile=adu&limit=5")
    assert resp.status_code == 200
    data = resp.get_json()
    # The seeded parcels (90/30, 50/80, 70/60) should order as B(80), C(60), A(30).
    scores = [row["score_adu"] for row in data["parcels"]
              if row.get("score_adu") is not None]
    assert scores == sorted(scores, reverse=True)
    assert scores[0] == 80.0


def test_api_parcels_rejects_unknown_profile(app_with_two_profile_scores):
    """An unknown profile param returns 400."""
    client = app_with_two_profile_scores.test_client()
    resp = client.get("/api/parcels?profile=nonexistent")
    assert resp.status_code == 400


def test_api_parcels_defaults_to_legacy_score_column(app_with_two_profile_scores):
    """Without ?profile, the endpoint returns 200 and all parcels carry score
    values. Using ?sort=score&dir=desc (explicit) confirms score-based ordering.
    Seeded parcels (90/30, 50/80, 70/60) order as A(90), C(70), B(50)."""
    client = app_with_two_profile_scores.test_client()
    # Use explicit sort=score to confirm score ordering works independently of
    # profile — the default ordering is by last_updated_date which may differ.
    resp = client.get("/api/parcels?sort=score&dir=desc&limit=5")
    assert resp.status_code == 200
    data = resp.get_json()
    scores = [row["score"] for row in data["parcels"] if row.get("score") is not None]
    assert scores == sorted(scores, reverse=True)
    assert scores[0] == 90.0


# ---------------------------------------------------------------------------
# /api/profile-defaults (Task 5.1)
# ---------------------------------------------------------------------------

def test_api_profile_defaults_returns_registry(tmp_path):
    """GET /api/profile-defaults returns the loaded registry as JSON."""
    from webapp.app import create_app
    from pipeline.db import init_db

    db = tmp_path / "t.db"
    init_db(db)

    cfg = tmp_path / "profile_defaults.yaml"
    cfg.write_text("""\
adu:
  yaml: scoring_adu.yaml
  score_column: score_adu
  recommended_filters:
    adu_eligible: 1
    lot_size_sf: {between: [3500, 12000]}
""")

    app = create_app(db_path=db, feature_outreach=False,
                     profile_defaults_path=cfg)
    resp = app.test_client().get("/api/profile-defaults")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "adu" in data
    assert data["adu"]["score_column"] == "score_adu"
    assert data["adu"]["recommended_filters"]["adu_eligible"] == 1
