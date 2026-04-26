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


@responses.activate
def test_values_handles_zero_prior_total_without_dividing_by_zero(db_path, geo, cook_client):
    """Vacant lots and certain class-100 rows can carry 0 in board_tot/certified_tot.
    The trend math must skip those years rather than divide by zero."""
    parcels_fixture = json.loads((FIXTURES / "assessor_parcels.json").read_text())
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_parcels.DATASET_ID}.json",
        json=parcels_fixture, status=200)
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_parcels.DATASET_ID}.json",
        json=[], status=200)
    assessor_parcels.fetch(geo, db_path, cook_client)

    # Inline fixture: latest year has data, prior year has 0
    vals = [
        {"pin": "14210010010000", "year": "2025",
         "board_tot": "300000", "board_land": "100000", "board_bldg": "200000",
         "certified_tot": "300000", "certified_land": "100000", "certified_bldg": "200000",
         "mailed_tot": "300000", "mailed_land": "100000", "mailed_bldg": "200000",
         "board_hie": "0"},
        {"pin": "14210010010000", "year": "2024",
         "board_tot": "0", "board_land": "0", "board_bldg": "0",
         "certified_tot": "0", "certified_land": "0", "certified_bldg": "0",
         "mailed_tot": "0", "mailed_land": "0", "mailed_bldg": "0", "board_hie": "0"},
    ]
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_values.DATASET_ID}.json",
        json=vals, status=200)
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_values.DATASET_ID}.json",
        json=[], status=200)
    # Must not raise
    assessor_values.fetch(geo, db_path, cook_client)

    conn = get_connection(db_path)
    p = conn.execute(
        "SELECT assessed_total, tax_increase_pct_1yr FROM parcels WHERE pin='14210010010000'"
    ).fetchone()
    assert p["assessed_total"] == 300000.0
    # 2024 is 0 → no usable prior-year for trend
    assert p["tax_increase_pct_1yr"] is None


@responses.activate
def test_values_writes_estimated_annual_tax(db_path, geo, cook_client):
    """End-to-end: estimated_annual_tax populated using config/tax_constants.yaml.
    With no exemption applied (homeowner exemption deferred until per-parcel
    exemptions data is ingested), the estimate is an upper bound on actual tax."""
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
        SELECT assessed_total, estimated_annual_tax FROM parcels
        WHERE pin='14210010010000'
    """).fetchone()
    # AV = 287430; EAV = 287430 × 3.0027 = 863066.061
    # tax = 863066.061 × 6.717% = 57,972.15
    assert p["assessed_total"] == 287430.0
    assert round(p["estimated_annual_tax"], 0) == 57972
