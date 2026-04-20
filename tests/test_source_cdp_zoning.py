import json
import responses
from sources import assessor_parcels, assessor_characteristics, cdp_zoning
from pipeline.db import get_connection
from tests.conftest import FIXTURES


@responses.activate
def test_cdp_zoning_assigns_zone_class_and_far_gap(db_path, geo, cook_client, cdp_client):
    # Seed parcels and characteristics
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

    # Now zoning
    zf = json.loads((FIXTURES / "cdp_zoning.json").read_text())
    responses.add(responses.GET,
        f"https://data.cityofchicago.org/resource/{cdp_zoning.DATASET_ID}.json",
        json=zf, status=200)
    responses.add(responses.GET,
        f"https://data.cityofchicago.org/resource/{cdp_zoning.DATASET_ID}.json",
        json=[], status=200)
    cdp_zoning.fetch(geo, db_path, cdp_client)

    conn = get_connection(db_path)
    p = conn.execute("""
        SELECT zone_class, max_far, built_far, far_gap, allows_multifamily_by_right
        FROM parcels WHERE pin='14210010010000'
    """).fetchone()
    assert p["zone_class"] == "RT-4"
    assert p["max_far"] == 1.2
    assert p["built_far"] == 0.64
    assert round(p["far_gap"], 2) == 1.88
    assert p["allows_multifamily_by_right"] == 1
