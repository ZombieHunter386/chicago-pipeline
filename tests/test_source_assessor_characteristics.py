import json
import responses
from sources import assessor_parcels, assessor_characteristics
from pipeline.db import get_connection
from tests.conftest import FIXTURES


@responses.activate
def test_characteristics_populates_building_facts(db_path, geo, cook_client):
    """After GIS-first lot sizing, characteristics writes only the building
    facts (building_sf, year_built, condition, classification). lot_size_sf
    and built_far are now derived from ccgis_parcels — see that source's
    test for the lot+built_far flow."""
    parcels_fixture = json.loads((FIXTURES / "assessor_parcels.json").read_text())
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_parcels.DATASET_ID}.json",
        json=parcels_fixture, status=200)
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_parcels.DATASET_ID}.json",
        json=[], status=200)
    assessor_parcels.fetch(geo, db_path, cook_client)

    char_fixture = json.loads((FIXTURES / "assessor_characteristics.json").read_text())
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_characteristics.DATASET_ID}.json",
        json=char_fixture, status=200)
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_characteristics.DATASET_ID}.json",
        json=[], status=200)
    n = assessor_characteristics.fetch(geo, db_path, cook_client)
    # 3 distinct (pin, year) rows after multi-card aggregation
    # (010 single-card, 020 single-card, 030 two cards collapsed to one)
    assert n == 3

    conn = get_connection(db_path)
    p = conn.execute("""
        SELECT pin, lot_size_sf, building_sf, year_built, condition, built_far,
               building_classification
        FROM parcels WHERE pin='14210010010000'
    """).fetchone()
    # lot_size_sf and built_far are NOT written by characteristics anymore
    # — they're set by ccgis_parcels and then recomputed.
    assert p["lot_size_sf"] is None
    assert p["built_far"] is None
    # Building-fact columns still come from characteristics.
    assert p["building_sf"] == 2400.0
    assert p["year_built"] == 1923
    assert p["condition"] == "Fair"
    assert p["building_classification"] == "2 Story"


@responses.activate
def test_characteristics_takes_largest_bldg_sf_across_multicards(db_path, geo, cook_client):
    """A multi-card parcel (e.g. 2-flat + coach house) returns one row per
    card from Socrata. The aggregated building_sf takes the LARGEST card's
    value (not the sum), so the assessor and Chicago footprints datasets
    are apples-to-apples for the merge. Other fields likewise come from the
    primary card."""
    parcels_fixture = json.loads((FIXTURES / "assessor_parcels.json").read_text())
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_parcels.DATASET_ID}.json",
        json=parcels_fixture, status=200)
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_parcels.DATASET_ID}.json",
        json=[], status=200)
    assessor_parcels.fetch(geo, db_path, cook_client)

    char_fixture = json.loads((FIXTURES / "assessor_characteristics.json").read_text())
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_characteristics.DATASET_ID}.json",
        json=char_fixture, status=200)
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_characteristics.DATASET_ID}.json",
        json=[], status=200)
    assessor_characteristics.fetch(geo, db_path, cook_client)

    conn = get_connection(db_path)
    p = conn.execute("""
        SELECT building_sf, year_built, building_classification, condition
        FROM parcels WHERE pin='14210010030000'
    """).fetchone()
    # Card 1: class=211 2-flat 3645 sf, Card 2: class=202 coach house 400 sf
    # → primary card = 211 (largest bldg_sf), so building_sf = 3645
    assert p["building_sf"] == 3645.0
    assert p["year_built"] == 1903
    assert p["building_classification"] == "2 Story"  # from primary card

    # Raw table stores the same primary-card value
    raw = conn.execute(
        "SELECT char_bldg_sf, class, pin_is_multicard, pin_num_cards "
        "FROM raw_assessor_characteristics WHERE pin='14210010030000'"
    ).fetchone()
    assert raw["char_bldg_sf"] == 3645.0
    assert raw["class"] == "211"
    assert raw["pin_is_multicard"] == 1
    assert raw["pin_num_cards"] == 2
