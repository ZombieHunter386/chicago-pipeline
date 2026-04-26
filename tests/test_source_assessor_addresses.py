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
    assert n == 3

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


def test_is_llc_detects_dotted_forms():
    """Real assessor data uses dotted forms like 'L.L.C.' and 'L.P.'."""
    from sources.assessor_addresses import is_llc
    assert is_llc("ABC L.L.C.") is True
    assert is_llc("Smith L.P.") is True
    assert is_llc("XYZ L.L.P.") is True
    assert is_llc("Acme Inc.") is True
    assert is_llc("BigCo Corp.") is True


def test_is_absentee_normalizes_addresses():
    from sources.assessor_addresses import is_absentee
    assert is_absentee("100 W DIVERSEY", "PO BOX 4421") is True
    assert is_absentee("100 W DIVERSEY", "100 W DIVERSEY") is False
    assert is_absentee("100 w diversey", "100 W DIVERSEY") is False  # case-insensitive
    assert is_absentee("100 W DIVERSEY ", " 100 W DIVERSEY") is False  # trim
    assert is_absentee(None, "100 W DIVERSEY") is False


def test_is_absentee_handles_pkwy_pky_suffix_variants():
    from sources.assessor_addresses import is_absentee
    # Confirmed false-positive case from smoke.db pin 14292270501001
    assert is_absentee("1122 W DIVERSEY PKY 1E", "1122 W DIVERSEY PKWY1E") is False
    assert is_absentee("1122 W DIVERSEY PKY 2E", "1122 W DIVERSEY PKWY2E") is False


def test_is_absentee_handles_full_suffix_words():
    from sources.assessor_addresses import is_absentee
    assert is_absentee("100 W DIVERSEY AVE", "100 W DIVERSEY AVENUE") is False
    assert is_absentee("100 W STATE ST", "100 W STATE STREET") is False
    assert is_absentee("100 N CLARK BLVD", "100 N CLARK BOULEVARD") is False


def test_is_absentee_handles_direction_variants():
    from sources.assessor_addresses import is_absentee
    assert is_absentee("100 NORTH STATE ST", "100 N STATE ST") is False
    assert is_absentee("100 W DIVERSEY AVE", "100 WEST DIVERSEY AVE") is False


def test_is_absentee_strips_unit_markers():
    from sources.assessor_addresses import is_absentee
    assert is_absentee("100 W DIVERSEY UNIT 5", "100 W DIVERSEY") is False
    assert is_absentee("100 W DIVERSEY APT 5", "100 W DIVERSEY UNIT 6") is False
    assert is_absentee("100 W DIVERSEY STE 200", "100 W DIVERSEY") is False
    assert is_absentee("100 W DIVERSEY #5", "100 W DIVERSEY") is False


def test_is_absentee_still_detects_real_absentee():
    from sources.assessor_addresses import is_absentee
    assert is_absentee("1222 W DIVERSEY PKWY", "PO BOX 4421") is True
    assert is_absentee("100 W DIVERSEY AVE", "200 W DIVERSEY AVE") is True
    assert is_absentee("100 W DIVERSEY AVE", "100 W ARMITAGE AVE") is True


@responses.activate
def test_is_llc_checks_owner_field_when_mail_is_a_person(db_path, geo, cook_client):
    """LLC ownership often shows on the owner_address_name line while
    mail_address_name is a person (e.g. property manager). Must flag is_llc=1."""
    parcels_fixture = json.loads((FIXTURES / "assessor_parcels.json").read_text())
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_parcels.DATASET_ID}.json",
        json=parcels_fixture, status=200)
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_parcels.DATASET_ID}.json",
        json=[], status=200)
    assessor_parcels.fetch(geo, db_path, cook_client)

    addr_fixture = json.loads((FIXTURES / "assessor_addresses.json").read_text())
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_addresses.DATASET_ID}.json",
        json=addr_fixture, status=200)
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_addresses.DATASET_ID}.json",
        json=[], status=200)
    assessor_addresses.fetch(geo, db_path, cook_client)

    conn = get_connection(db_path)
    p = conn.execute(
        "SELECT is_llc, owner_name FROM parcels WHERE pin='14210010040000'"
    ).fetchone()
    assert p["is_llc"] == 1
    assert "LLC" in (p["owner_name"] or "")
