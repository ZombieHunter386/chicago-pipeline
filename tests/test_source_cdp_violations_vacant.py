import json
import responses
from datetime import date
from sources import assessor_parcels, cdp_violations, cdp_vacant
from pipeline.db import get_connection
from tests.conftest import FIXTURES


def _seed(db_path, geo, cook_client):
    pf = json.loads((FIXTURES / "assessor_parcels.json").read_text())
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_parcels.DATASET_ID}.json",
        json=pf, status=200)
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_parcels.DATASET_ID}.json",
        json=[], status=200)
    assessor_parcels.fetch(geo, db_path, cook_client)


@responses.activate
def test_violations_count_and_oldest(db_path, geo, cook_client, cdp_client, monkeypatch):
    monkeypatch.setattr(cdp_violations, "TODAY", date(2026, 4, 19))
    _seed(db_path, geo, cook_client)
    fx = json.loads((FIXTURES / "cdp_violations.json").read_text())
    responses.add(responses.GET,
        f"https://data.cityofchicago.org/resource/{cdp_violations.DATASET_ID}.json",
        json=fx, status=200)
    responses.add(responses.GET,
        f"https://data.cityofchicago.org/resource/{cdp_violations.DATASET_ID}.json",
        json=[], status=200)
    cdp_violations.fetch(geo, db_path, cdp_client)

    conn = get_connection(db_path)
    p = conn.execute("SELECT open_violations_count, oldest_violation_age_days FROM parcels WHERE pin='14210010010000'").fetchone()
    assert p[0] == 2
    assert 1000 <= p[1] <= 1120


@responses.activate
def test_vacant_flag(db_path, geo, cook_client, cdp_client):
    _seed(db_path, geo, cook_client)
    fx = json.loads((FIXTURES / "cdp_vacant.json").read_text())
    responses.add(responses.GET,
        f"https://data.cityofchicago.org/resource/{cdp_vacant.DATASET_ID}.json",
        json=fx, status=200)
    responses.add(responses.GET,
        f"https://data.cityofchicago.org/resource/{cdp_vacant.DATASET_ID}.json",
        json=[], status=200)
    cdp_vacant.fetch(geo, db_path, cdp_client)
    conn = get_connection(db_path)
    p = conn.execute("SELECT has_vacancy_report FROM parcels WHERE pin='14210010020000'").fetchone()[0]
    assert p == 1
