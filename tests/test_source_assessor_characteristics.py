import json
import responses
from sources import assessor_parcels, assessor_characteristics
from pipeline.db import get_connection
from tests.conftest import FIXTURES


@responses.activate
def test_characteristics_populates_building_facts_and_built_far(db_path, geo, cook_client):
    parcels_fixture = json.loads((FIXTURES / "assessor_parcels.json").read_text())
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_parcels.DATASET_ID}.json",
        json=parcels_fixture, status=200)
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_parcels.DATASET_ID}.json",
        json=[], status=200)
    assessor_parcels.fetch(geo, db_path, cook_client)

    char_fixture = json.loads((FIXTURES / "assessor_characteristics.json").read_text())
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_characteristics.DATASET_ID}.json",
        json=char_fixture, status=200)
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_characteristics.DATASET_ID}.json",
        json=[], status=200)
    n = assessor_characteristics.fetch(geo, db_path, cook_client)
    assert n == 2

    conn = get_connection(db_path)
    p = conn.execute("""
        SELECT pin, lot_size_sf, building_sf, year_built, condition, built_far,
               building_classification
        FROM parcels WHERE pin='14210010010000'
    """).fetchone()
    assert p["lot_size_sf"] == 3750.0
    assert p["building_sf"] == 2400.0
    assert p["year_built"] == 1923
    assert p["condition"] == "Fair"
    assert p["built_far"] == 0.64  # 2400/3750 rounded to 2dp
    assert p["building_classification"] == "2 Story"
