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
