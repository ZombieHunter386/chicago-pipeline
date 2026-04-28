"""Tests for sources.cdp_scofflaw — Building Code Scofflaw List."""
import json
import responses
from sources import assessor_parcels, assessor_addresses, cdp_scofflaw
from pipeline.db import get_connection
from tests.conftest import FIXTURES


def _seed_parcels(geo, db_path, cook_client):
    pf = json.loads((FIXTURES / "assessor_parcels.json").read_text())
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_parcels.DATASET_ID}.json",
        json=pf, status=200)
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_parcels.DATASET_ID}.json",
        json=[], status=200)
    assessor_parcels.fetch(geo, db_path, cook_client)

    af = json.loads((FIXTURES / "assessor_addresses.json").read_text())
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_addresses.DATASET_ID}.json",
        json=af, status=200)
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_addresses.DATASET_ID}.json",
        json=[], status=200)
    assessor_addresses.fetch(geo, db_path, cook_client)


@responses.activate
def test_scofflaw_aggregates_appearances_per_pin(db_path, geo, cook_client, cdp_client):
    _seed_parcels(geo, db_path, cook_client)

    sf = json.loads((FIXTURES / "cdp_scofflaw.json").read_text())
    responses.add(responses.GET,
        f"https://data.cityofchicago.org/resource/{cdp_scofflaw.DATASET_ID}.json",
        json=sf, status=200)
    responses.add(responses.GET,
        f"https://data.cityofchicago.org/resource/{cdp_scofflaw.DATASET_ID}.json",
        json=[], status=200)
    cdp_scofflaw.fetch(geo, db_path, cdp_client)

    conn = get_connection(db_path)
    row = conn.execute(
        "SELECT is_scofflaw, scofflaw_appearances_count, "
        "       most_recent_scofflaw_list_date "
        "FROM parcels WHERE pin = '14210010010000'"
    ).fetchone()
    assert row["is_scofflaw"] == 1
    # Two records in fixture for "100 W DIVERSEY"
    assert row["scofflaw_appearances_count"] == 2
    assert row["most_recent_scofflaw_list_date"] == "2025-09-01"

    # Other parcels not on the list
    other = conn.execute(
        "SELECT is_scofflaw FROM parcels WHERE pin = '14210010020000'"
    ).fetchone()
    assert other["is_scofflaw"] in (None, 0)


@responses.activate
def test_scofflaw_clears_stale_flags_on_rerun(db_path, geo, cook_client, cdp_client):
    _seed_parcels(geo, db_path, cook_client)

    # First run sets the flag
    sf = json.loads((FIXTURES / "cdp_scofflaw.json").read_text())
    responses.add(responses.GET,
        f"https://data.cityofchicago.org/resource/{cdp_scofflaw.DATASET_ID}.json",
        json=sf, status=200)
    responses.add(responses.GET,
        f"https://data.cityofchicago.org/resource/{cdp_scofflaw.DATASET_ID}.json",
        json=[], status=200)
    cdp_scofflaw.fetch(geo, db_path, cdp_client)

    # Second run with empty list — flag should clear
    responses.add(responses.GET,
        f"https://data.cityofchicago.org/resource/{cdp_scofflaw.DATASET_ID}.json",
        json=[], status=200)
    responses.add(responses.GET,
        f"https://data.cityofchicago.org/resource/{cdp_scofflaw.DATASET_ID}.json",
        json=[], status=200)
    cdp_scofflaw.fetch(geo, db_path, cdp_client)

    conn = get_connection(db_path)
    row = conn.execute(
        "SELECT is_scofflaw, scofflaw_appearances_count "
        "FROM parcels WHERE pin = '14210010010000'"
    ).fetchone()
    assert row["is_scofflaw"] == 0
    assert row["scofflaw_appearances_count"] == 0
