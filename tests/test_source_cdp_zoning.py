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

    # Insert a parcel inside the geo polygon but OUTSIDE the zoning polygon
    # (zoning bbox is lng -87.67..-87.62; this one sits at lng -87.68).
    conn = get_connection(db_path)
    conn.execute(
        "INSERT INTO parcels (pin, lat, lng, first_seen_date, last_updated_date, stage) "
        "VALUES ('OUTSIDE_ZONE', 41.93, -87.68, '2026-04-19', '2026-04-19', 'scored')"
    )
    conn.commit()
    conn.close()

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

    # Parcel outside any zoning polygon: must write NULL (not "nan" string, not 0)
    out = conn.execute("""
        SELECT zone_class, max_far, far_gap, allows_multifamily_by_right
        FROM parcels WHERE pin='OUTSIDE_ZONE'
    """).fetchone()
    assert out["zone_class"] is None
    assert out["max_far"] is None
    assert out["far_gap"] is None
    assert out["allows_multifamily_by_right"] is None
