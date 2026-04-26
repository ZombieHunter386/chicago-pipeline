import json
import responses
from sources import assessor_parcels, assessor_values
from pipeline.db import get_connection
from tests.conftest import FIXTURES


@responses.activate
def test_values_populates_assessed_and_trends(db_path, geo, cook_client):
    parcels_fixture = json.loads((FIXTURES / "assessor_parcels.json").read_text())
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_parcels.DATASET_ID}.json",
        json=parcels_fixture, status=200)
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_parcels.DATASET_ID}.json",
        json=[], status=200)
    assessor_parcels.fetch(geo, db_path, cook_client)

    vals_fixture = json.loads((FIXTURES / "assessor_values.json").read_text())
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_values.DATASET_ID}.json",
        json=vals_fixture, status=200)
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_values.DATASET_ID}.json",
        json=[], status=200)
    assessor_values.fetch(geo, db_path, cook_client)

    conn = get_connection(db_path)
    p = conn.execute("""
        SELECT assessed_total, assessed_land, land_building_ratio,
               tax_increase_pct_1yr, tax_increase_pct_5yr
        FROM parcels WHERE pin='14210010010000'
    """).fetchone()
    assert p["assessed_total"] == 287430.0
    assert p["assessed_land"] == 192430.0
    assert round(p["land_building_ratio"], 2) == 0.67
    assert round(p["tax_increase_pct_1yr"], 1) == 8.5
    assert round(p["tax_increase_pct_5yr"], 1) == 34.3


@responses.activate
def test_values_falls_back_when_latest_year_is_empty(db_path, geo, cook_client):
    """Latest tax year (e.g. 2026) has Board-of-Review NULL because BOR hasn't
    published yet. Pipeline must fall back to the latest year that has any
    populated total, using board → certified → mailed precedence."""
    parcels_fixture = json.loads((FIXTURES / "assessor_parcels.json").read_text())
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_parcels.DATASET_ID}.json",
        json=parcels_fixture, status=200)
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_parcels.DATASET_ID}.json",
        json=[], status=200)
    assessor_parcels.fetch(geo, db_path, cook_client)

    vals_fixture = json.loads((FIXTURES / "assessor_values.json").read_text())
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_values.DATASET_ID}.json",
        json=vals_fixture, status=200)
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_values.DATASET_ID}.json",
        json=[], status=200)
    assessor_values.fetch(geo, db_path, cook_client)

    conn = get_connection(db_path)
    p = conn.execute("""
        SELECT assessed_total, assessed_land, assessed_building,
               tax_increase_pct_1yr, tax_increase_pct_5yr
        FROM parcels WHERE pin='14210010030000'
    """).fetchone()
    # 2026 is all-NULL; latest non-null year is 2025 (board_tot=100000)
    assert p["assessed_total"] == 100000.0
    assert p["assessed_land"] == 40000.0
    assert p["assessed_building"] == 60000.0
    # 1-yr trend: 2025 (100000) vs 2024 (92000) = ~8.7%
    assert round(p["tax_increase_pct_1yr"], 1) == 8.7
    # 5-yr trend: 2025 (100000) vs 2020 (74000) = ~35.1%
    assert round(p["tax_increase_pct_5yr"], 1) == 35.1
