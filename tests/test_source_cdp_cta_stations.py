import json
import responses
from sources import assessor_parcels, cdp_cta_stations
from pipeline.db import get_connection
from tests.conftest import FIXTURES


@responses.activate
def test_cta_distances_populated(db_path, geo, cook_client, cdp_client):
    pf = json.loads((FIXTURES / "assessor_parcels.json").read_text())
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_parcels.DATASET_ID}.json",
        json=pf, status=200)
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_parcels.DATASET_ID}.json",
        json=[], status=200)
    assessor_parcels.fetch(geo, db_path, cook_client)

    fx = json.loads((FIXTURES / "cdp_cta_stations.json").read_text())
    responses.add(responses.GET,
        f"https://data.cityofchicago.org/resource/{cdp_cta_stations.DATASET_ID}.json",
        json=fx, status=200)
    responses.add(responses.GET,
        f"https://data.cityofchicago.org/resource/{cdp_cta_stations.DATASET_ID}.json",
        json=[], status=200)
    cdp_cta_stations.fetch(geo, db_path, cdp_client)

    conn = get_connection(db_path)
    p = conn.execute("""
        SELECT cta_nearest_station, cta_distance_ft FROM parcels WHERE pin='14210010010000'
    """).fetchone()
    assert p["cta_nearest_station"] in ("Belmont", "Diversey")
    assert p["cta_distance_ft"] is not None
    assert 0 < p["cta_distance_ft"] < 10000
