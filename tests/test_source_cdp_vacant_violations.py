"""Tests for sources.cdp_vacant_violations — Vacant/Abandoned Building Violations."""
import json
import responses
from sources import assessor_parcels, assessor_addresses, cdp_vacant_violations
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
def test_vacant_violations_aggregate_per_pin(db_path, geo, cook_client, cdp_client):
    _seed_parcels(geo, db_path, cook_client)

    vv = json.loads((FIXTURES / "cdp_vacant_violations.json").read_text())
    responses.add(responses.GET,
        f"https://data.cityofchicago.org/resource/{cdp_vacant_violations.DATASET_ID}.json",
        json=vv, status=200)
    responses.add(responses.GET,
        f"https://data.cityofchicago.org/resource/{cdp_vacant_violations.DATASET_ID}.json",
        json=[], status=200)
    cdp_vacant_violations.fetch(geo, db_path, cdp_client)

    conn = get_connection(db_path)
    row = conn.execute(
        "SELECT vacant_violations_count, vacant_violations_amount_due, "
        "       most_recent_vacant_violation_date "
        "FROM parcels WHERE pin = '14210010010000'"
    ).fetchone()
    assert row["vacant_violations_count"] == 2
    # 550 + 275 = 825
    assert abs(row["vacant_violations_amount_due"] - 825.0) < 0.01
    assert row["most_recent_vacant_violation_date"] == "2025-02-10"


@responses.activate
def test_vacant_violations_writes_raw_table(db_path, geo, cook_client, cdp_client):
    _seed_parcels(geo, db_path, cook_client)

    vv = json.loads((FIXTURES / "cdp_vacant_violations.json").read_text())
    responses.add(responses.GET,
        f"https://data.cityofchicago.org/resource/{cdp_vacant_violations.DATASET_ID}.json",
        json=vv, status=200)
    responses.add(responses.GET,
        f"https://data.cityofchicago.org/resource/{cdp_vacant_violations.DATASET_ID}.json",
        json=[], status=200)
    cdp_vacant_violations.fetch(geo, db_path, cdp_client)

    conn = get_connection(db_path)
    raw = conn.execute("SELECT * FROM raw_cdp_vacant_violations").fetchall()
    assert len(raw) == 2
    docket_numbers = sorted(r["docket_number"] for r in raw)
    assert docket_numbers == ["VV-LP-001", "VV-LP-002"]
    # Field mapping for the trailing-underscore source field
    assert raw[0]["entity_or_person"] in ("ACME PROPERTIES LLC", None)
