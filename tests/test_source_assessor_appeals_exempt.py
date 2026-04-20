import json
import responses
from sources import assessor_parcels, assessor_appeals, assessor_exempt
from pipeline.db import get_connection
from tests.conftest import FIXTURES


def _seed_parcels(db_path, geo, cook_client):
    fixture = json.loads((FIXTURES / "assessor_parcels.json").read_text())
    # Add an extra parcel to test exempt case
    fixture.append({"pin": "14210010030000", "year": "2025", "pin10": "1421001003",
                    "class": "320", "lat": "41.94", "lon": "-87.65", "ward_num": "44",
                    "zip_code": "60657", "tax_tif_district_num": None,
                    "tax_tif_district_name": None, "township_code": "76", "nbhd_code": "10"})
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_parcels.DATASET_ID}.json",
        json=fixture, status=200)
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_parcels.DATASET_ID}.json",
        json=[], status=200)
    assessor_parcels.fetch(geo, db_path, cook_client)


@responses.activate
def test_appeals_count_per_pin(db_path, geo, cook_client):
    _seed_parcels(db_path, geo, cook_client)
    fx = json.loads((FIXTURES / "assessor_appeals.json").read_text())
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_appeals.DATASET_ID}.json",
        json=fx, status=200)
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_appeals.DATASET_ID}.json",
        json=[], status=200)
    assessor_appeals.fetch(geo, db_path, cook_client)
    conn = get_connection(db_path)
    n = conn.execute("SELECT appeal_count FROM parcels WHERE pin='14210010010000'").fetchone()[0]
    assert n == 2


@responses.activate
def test_exempt_pins_stored(db_path, geo, cook_client):
    _seed_parcels(db_path, geo, cook_client)
    fx = json.loads((FIXTURES / "assessor_exempt.json").read_text())
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_exempt.DATASET_ID}.json",
        json=fx, status=200)
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_exempt.DATASET_ID}.json",
        json=[], status=200)
    assessor_exempt.fetch(geo, db_path, cook_client)
    conn = get_connection(db_path)
    rows = conn.execute("SELECT pin, exemption_type FROM raw_assessor_exempt").fetchall()
    assert (rows[0][0], rows[0][1]) == ("14210010030000", "Religious")
