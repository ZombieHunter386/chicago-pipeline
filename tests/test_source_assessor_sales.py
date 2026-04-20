import json
import responses
from datetime import date
from sources import assessor_parcels, assessor_sales
from pipeline.db import get_connection
from tests.conftest import FIXTURES


@responses.activate
def test_sales_populates_last_sale_and_hold_duration(db_path, geo, cook_client, monkeypatch):
    monkeypatch.setattr(assessor_sales, "TODAY", date(2026, 4, 19))

    parcels_fixture = json.loads((FIXTURES / "assessor_parcels.json").read_text())
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_parcels.DATASET_ID}.json",
        json=parcels_fixture, status=200)
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_parcels.DATASET_ID}.json",
        json=[], status=200)
    assessor_parcels.fetch(geo, db_path, cook_client)

    sales_fixture = json.loads((FIXTURES / "assessor_sales.json").read_text())
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_sales.DATASET_ID}.json",
        json=sales_fixture, status=200)
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_sales.DATASET_ID}.json",
        json=[], status=200)
    assessor_sales.fetch(geo, db_path, cook_client)

    conn = get_connection(db_path)
    p1 = conn.execute("""
        SELECT last_sale_date, last_sale_price, hold_duration_years, deed_type
        FROM parcels WHERE pin='14210010010000'
    """).fetchone()
    assert p1["last_sale_date"] == "2004-03-15"
    assert p1["last_sale_price"] == 385000.0
    # 2026-04-19 - 2004-03-15 ≈ 22.1 years
    assert 22.0 <= p1["hold_duration_years"] <= 22.2
    assert p1["deed_type"] == "Warranty"
