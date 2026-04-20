import json
import responses
from datetime import date
from sources import assessor_parcels, cdp_permits
from pipeline.db import get_connection
from tests.conftest import FIXTURES


@responses.activate
def test_permits_compute_years_since_last_permit(db_path, geo, cook_client, cdp_client, monkeypatch):
    monkeypatch.setattr(cdp_permits, "TODAY", date(2026, 4, 19))

    pf = json.loads((FIXTURES / "assessor_parcels.json").read_text())
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_parcels.DATASET_ID}.json",
        json=pf, status=200)
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_parcels.DATASET_ID}.json",
        json=[], status=200)
    assessor_parcels.fetch(geo, db_path, cook_client)

    pm = json.loads((FIXTURES / "cdp_permits.json").read_text())
    responses.add(responses.GET,
        f"https://data.cityofchicago.org/resource/{cdp_permits.DATASET_ID}.json",
        json=pm, status=200)
    responses.add(responses.GET,
        f"https://data.cityofchicago.org/resource/{cdp_permits.DATASET_ID}.json",
        json=[], status=200)
    cdp_permits.fetch(geo, db_path, cdp_client)

    conn = get_connection(db_path)
    p1 = conn.execute("SELECT years_since_last_permit FROM parcels WHERE pin='14210010010000'").fetchone()
    # 2026-04-19 - 2018-05-12 ≈ 7.94 years
    assert 7.5 <= p1[0] <= 8.5
    p2 = conn.execute("SELECT years_since_last_permit FROM parcels WHERE pin='14210010020000'").fetchone()
    # 2026-04-19 - 2022-09-30 ≈ 3.55 years
    assert 3.0 <= p2[0] <= 4.0
