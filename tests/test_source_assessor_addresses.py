import json
import responses
from sources import assessor_parcels, assessor_addresses
from pipeline.db import get_connection
from tests.conftest import FIXTURES


@responses.activate
def test_addresses_populates_owner_and_derives_absentee_llc(db_path, geo, cook_client):
    # Seed parcels first using the parcels fetcher
    parcels_fixture = json.loads((FIXTURES / "assessor_parcels.json").read_text())
    responses.add(
        responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_parcels.DATASET_ID}.json",
        json=parcels_fixture, status=200,
    )
    responses.add(
        responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_parcels.DATASET_ID}.json",
        json=[], status=200,
    )
    assessor_parcels.fetch(geo, db_path, cook_client)

    # Now fetch addresses
    addr_fixture = json.loads((FIXTURES / "assessor_addresses.json").read_text())
    responses.add(
        responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_addresses.DATASET_ID}.json",
        json=addr_fixture, status=200,
    )
    responses.add(
        responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_addresses.DATASET_ID}.json",
        json=[], status=200,
    )
    n = assessor_addresses.fetch(geo, db_path, cook_client)
    assert n == 2

    conn = get_connection(db_path)
    rows = conn.execute("""
        SELECT pin, owner_name, mail_address, is_absentee, is_llc, address
        FROM parcels ORDER BY pin
    """).fetchall()
    by_pin = {r["pin"]: r for r in rows}
    # LLC + mail addr differs from prop addr → absentee + llc
    assert by_pin["14210010010000"]["is_absentee"] == 1
    assert by_pin["14210010010000"]["is_llc"] == 1
    assert by_pin["14210010010000"]["owner_name"] == "RACINE HOLDINGS LLC"
    assert by_pin["14210010010000"]["address"] == "100 W DIVERSEY PKWY"
    # Individual + mail addr matches prop addr → not absentee, not llc
    assert by_pin["14210010020000"]["is_absentee"] == 0
    assert by_pin["14210010020000"]["is_llc"] == 0


def test_is_llc_detects_common_patterns():
    from sources.assessor_addresses import is_llc
    assert is_llc("RACINE HOLDINGS LLC") is True
    assert is_llc("acme corp") is True
    assert is_llc("Smith Family Trust") is True
    assert is_llc("LP PARTNERS") is True
    assert is_llc("XYZ INC") is True
    assert is_llc("John Smith") is False
    assert is_llc(None) is False


def test_is_absentee_normalizes_addresses():
    from sources.assessor_addresses import is_absentee
    assert is_absentee("100 W DIVERSEY", "PO BOX 4421") is True
    assert is_absentee("100 W DIVERSEY", "100 W DIVERSEY") is False
    assert is_absentee("100 w diversey", "100 W DIVERSEY") is False  # case-insensitive
    assert is_absentee("100 W DIVERSEY ", " 100 W DIVERSEY") is False  # trim
    assert is_absentee(None, "100 W DIVERSEY") is False
