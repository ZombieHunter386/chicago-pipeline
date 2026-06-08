import json
import pytest
import responses
from pathlib import Path

from sources.chicago_adu_zones import derive_adu_eligible
from pipeline.db import init_db, get_connection


FIXTURES = Path(__file__).parent / "fixtures"


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


@responses.activate
def test_apply_to_parcels_sets_adu_eligible_and_restriction_text(tmp_path):
    """After fetch + apply_to_parcels:
      - RT/RM/B/C1/C2 parcels: adu_eligible=1, restriction_text=NULL (citywide)
      - RS parcels inside a polygon: adu_eligible=1, restriction_text=polygon's Text
      - RS parcels outside polygons: adu_eligible=0, restriction_text=NULL
      - M/PD/etc. parcels: adu_eligible=0, restriction_text=NULL
      - adu_has_annual_limits is 1 iff restriction_text contains 'Annual Limits'.
    """
    from sources.chicago_adu_zones import fetch, apply_to_parcels, ARCGIS_QUERY_URL

    db_path = tmp_path / "t.db"
    init_db(db_path)

    fx = json.loads((FIXTURES / "chicago_adu_zones.json").read_text())
    responses.add(responses.GET, ARCGIS_QUERY_URL, json=fx, status=200)
    fetch(db_path)

    # Get a point INSIDE the first polygon for the RS-in-polygon test.
    from shapely import wkt as wkt_lib
    conn = get_connection(db_path)
    first_polygon_wkt = conn.execute(
        "SELECT polygon_wkt FROM raw_chicago_adu_zones LIMIT 1"
    ).fetchone()["polygon_wkt"]
    inside = wkt_lib.loads(first_polygon_wkt).representative_point()
    inside_lat, inside_lng = inside.y, inside.x

    test_parcels = [
        # (pin, zone_class, lat, lng, expected_eligible, expected_restriction_substr)
        ("00000000000001", "RT-4",  41.95, -87.65, 1, None),
        ("00000000000002", "B3-2",  41.95, -87.65, 1, None),
        ("00000000000003", "C1-2",  41.95, -87.65, 1, None),
        ("00000000000004", "RS-3",  inside_lat, inside_lng, 1, "set"),
        ("00000000000005", "RS-3",  41.95, -87.65, 0, None),
        ("00000000000006", "M1-2",  41.95, -87.65, 0, None),
        ("00000000000007", "PD 853", 41.95, -87.65, 0, None),
    ]
    for pin, zc, lat, lng, _, _ in test_parcels:
        conn.execute(
            "INSERT INTO parcels(pin, pin10, zone_class, lat, lng) "
            "VALUES (?, ?, ?, ?, ?)",
            (pin, pin[:10], zc, lat, lng),
        )
    conn.commit()
    conn.close()

    apply_to_parcels(db_path)

    conn = get_connection(db_path)
    for pin, _, _, _, expected_eligible, expected_restriction in test_parcels:
        row = conn.execute(
            "SELECT adu_eligible, adu_restriction_text, adu_has_annual_limits "
            "FROM parcels WHERE pin=?", (pin,)
        ).fetchone()
        assert row["adu_eligible"] == expected_eligible, \
            f"pin {pin}: expected adu_eligible={expected_eligible}, got {row['adu_eligible']}"
        if expected_restriction is None:
            assert row["adu_restriction_text"] is None, \
                f"pin {pin}: expected NULL restriction, got {row['adu_restriction_text']!r}"
        else:
            assert row["adu_restriction_text"], \
                f"pin {pin}: expected restriction text to be set"
    conn.close()


@responses.activate
def test_apply_to_parcels_sets_has_annual_limits_flag(tmp_path):
    """adu_has_annual_limits is derived from restriction_text containing
    'Annual Limits'."""
    from sources.chicago_adu_zones import fetch, apply_to_parcels, ARCGIS_QUERY_URL
    db_path = tmp_path / "t.db"
    init_db(db_path)
    fx = json.loads((FIXTURES / "chicago_adu_zones.json").read_text())
    responses.add(responses.GET, ARCGIS_QUERY_URL, json=fx, status=200)
    fetch(db_path)

    from shapely import wkt as wkt_lib
    conn = get_connection(db_path)
    row = conn.execute(
        "SELECT polygon_wkt FROM raw_chicago_adu_zones "
        "WHERE restriction_text LIKE '%Annual Limits%' LIMIT 1"
    ).fetchone()
    if row is None:
        pytest.skip("fixture has no polygon with 'Annual Limits'")
    pt = wkt_lib.loads(row["polygon_wkt"]).representative_point()
    conn.execute(
        "INSERT INTO parcels(pin, pin10, zone_class, lat, lng) "
        "VALUES ('00000000000010', '0000000000', 'RS-3', ?, ?)",
        (pt.y, pt.x),
    )
    conn.commit()
    conn.close()

    apply_to_parcels(db_path)

    conn = get_connection(db_path)
    flag = conn.execute(
        "SELECT adu_has_annual_limits FROM parcels WHERE pin='00000000000010'"
    ).fetchone()[0]
    assert flag == 1
    conn.close()


@responses.activate
def test_fetch_writes_polygons_to_raw_table(tmp_path):
    """Fetching the ArcGIS layer persists each feature as a row in
    raw_chicago_adu_zones with zone_id, restriction_text, polygon_wkt."""
    from sources.chicago_adu_zones import fetch, ARCGIS_QUERY_URL

    db_path = tmp_path / "t.db"
    init_db(db_path)

    fx = json.loads((FIXTURES / "chicago_adu_zones.json").read_text())
    responses.add(responses.GET, ARCGIS_QUERY_URL, json=fx, status=200)

    n = fetch(db_path)
    assert n >= 1, "should write at least one polygon row"

    conn = get_connection(db_path)
    rows = conn.execute(
        "SELECT zone_id, restriction_text, polygon_wkt FROM raw_chicago_adu_zones"
    ).fetchall()
    assert len(rows) == n
    for r in rows:
        assert r["zone_id"]
        assert r["polygon_wkt"].startswith("POLYGON") or r["polygon_wkt"].startswith("MULTIPOLYGON")
    conn.close()
