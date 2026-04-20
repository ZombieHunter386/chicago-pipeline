# tests/test_source_assessor_parcels.py
import json
import responses
from sources import assessor_parcels
from pipeline.db import get_connection
from tests.conftest import FIXTURES


@responses.activate
def test_fetch_loads_parcels_in_geography_only(db_path, geo, cook_client):
    fixture = json.loads((FIXTURES / "assessor_parcels.json").read_text())
    responses.add(
        responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_parcels.DATASET_ID}.json",
        json=fixture, status=200,
    )
    responses.add(
        responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_parcels.DATASET_ID}.json",
        json=[], status=200,
    )
    n = assessor_parcels.fetch(geo, db_path, cook_client)
    # The 99999... row is far outside the polygon and must be dropped
    assert n == 2

    conn = get_connection(db_path)
    rows = conn.execute("SELECT pin FROM raw_assessor_parcels ORDER BY pin").fetchall()
    pins = [r[0] for r in rows]
    assert "14210010010000" in pins
    assert "99999990000000" not in pins

    # Parcels stub row should also exist for downstream join
    parcel_rows = conn.execute("SELECT pin, lat, lng, ward_num FROM parcels ORDER BY pin").fetchall()
    assert len(parcel_rows) == 2
    assert parcel_rows[0]["pin"] == "14210010010000"
    assert parcel_rows[0]["lat"] == 41.94
