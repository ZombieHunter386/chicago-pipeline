"""Tests for sources.cdp_building_footprints — frozen-2010 footprints with merge."""
import json
import responses
from sources import (
    assessor_parcels,
    assessor_addresses,
    assessor_characteristics,
    cdp_building_footprints,
)
from pipeline.db import get_connection
from tests.conftest import FIXTURES


def _seed(geo, db_path, cook_client):
    """Seed parcels + addresses + characteristics so the merge has assessor
    values to compare against."""
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

    cf = json.loads((FIXTURES / "assessor_characteristics.json").read_text())
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_characteristics.DATASET_ID}.json",
        json=cf, status=200)
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_characteristics.DATASET_ID}.json",
        json=[], status=200)
    assessor_characteristics.fetch(geo, db_path, cook_client)


def _run_footprints(geo, db_path, cdp_client):
    fp = json.loads((FIXTURES / "cdp_building_footprints.json").read_text())
    responses.add(responses.GET,
        f"https://data.cityofchicago.org/resource/{cdp_building_footprints.DATASET_ID}.json",
        json=fp, status=200)
    responses.add(responses.GET,
        f"https://data.cityofchicago.org/resource/{cdp_building_footprints.DATASET_ID}.json",
        json=[], status=200)
    cdp_building_footprints.fetch(geo, db_path, cdp_client)


@responses.activate
def test_assessor_wins_when_non_null(db_path, geo, cook_client, cdp_client):
    _seed(geo, db_path, cook_client)
    _run_footprints(geo, db_path, cdp_client)

    conn = get_connection(db_path)
    # Parcel 10000 has assessor: char_yrblt=1923, char_bldg_sf=2400, condition=Fair.
    # Footprint FP-MAIN-10 has bldg_sq_fo=2500, year_built=1920, no_of_unit=4, condition=NEEDS MINOR REPAIR.
    # Under "assessor wins" rule: keep assessor's year/sf/condition. Footprint
    # only fills NULLs — unit_count is NULL on assessor side, so footprint wins
    # there (=4).
    row = conn.execute(
        "SELECT year_built, building_sf, condition, unit_count, "
        "       building_sf_source, condition_source "
        "FROM parcels WHERE pin = '14210010010000'"
    ).fetchone()
    assert row["year_built"] == 1923          # assessor
    assert row["building_sf"] == 2400.0       # assessor
    assert row["condition"] == "Fair"         # assessor
    assert row["unit_count"] == 4             # footprint backstop (assessor NULL)
    assert row["building_sf_source"] == "assessor"
    assert row["condition_source"] == "assessor"


@responses.activate
def test_largest_area_structure_wins_over_garage(db_path, geo, cook_client, cdp_client):
    _seed(geo, db_path, cook_client)
    # Null out parcel 10000's assessor data so footprint actually wins, then
    # we can verify largest-area beats garage in the footprint reduction step.
    conn = get_connection(db_path)
    conn.execute(
        "UPDATE parcels SET building_sf=NULL, year_built=NULL, condition=NULL "
        "WHERE pin='14210010010000'"
    )
    conn.commit()
    conn.close()

    _run_footprints(geo, db_path, cdp_client)

    conn = get_connection(db_path)
    # FP-MAIN-10 (area=400, sf=2500, year=1920) and FP-GARAGE-10 (area=200,
    # sf=200, year=1980) both contain parcel 10000's centroid. With assessor
    # nulled, footprint fills — larger structure (main) wins.
    row = conn.execute(
        "SELECT building_sf, year_built, building_sf_source "
        "FROM parcels WHERE pin = '14210010010000'"
    ).fetchone()
    assert row["building_sf"] == 2500
    assert row["year_built"] == 1920
    assert row["building_sf_source"] == "footprint"


@responses.activate
def test_demolished_footprints_excluded(db_path, geo, cook_client, cdp_client):
    _seed(geo, db_path, cook_client)
    _run_footprints(geo, db_path, cdp_client)

    conn = get_connection(db_path)
    # FP-DEMOLISHED has bldg_sq_fo=9999 and same coords as FP-MAIN-10. If it
    # were not filtered, the largest-area-structure rule would pick it
    # (shape_area=9999) and parcel 10000 would have building_sf=9999.
    row = conn.execute(
        "SELECT building_sf FROM parcels WHERE pin = '14210010010000'"
    ).fetchone()
    assert row["building_sf"] != 9999

    # And it should not be in the raw table either.
    raw = conn.execute(
        "SELECT bldg_id FROM raw_cdp_building_footprints WHERE bldg_id='FP-DEMOLISHED'"
    ).fetchone()
    assert raw is None


@responses.activate
def test_assessor_kept_when_present_footprint_only_fills_nulls(db_path, geo, cook_client, cdp_client):
    _seed(geo, db_path, cook_client)
    _run_footprints(geo, db_path, cdp_client)

    conn = get_connection(db_path)
    # Parcel 20000: assessor has char_yrblt=1958, char_bldg_sf=3000.
    # Footprint FP-MAIN-20: year_built=0/null, bldg_sq_fo=4000, no_of_unit=8.
    # Under "assessor wins" rule: keep year_built=1958 and building_sf=3000.
    # unit_count is NULL on assessor side, so footprint backstops with 8.
    row = conn.execute(
        "SELECT year_built, building_sf, unit_count, building_sf_source "
        "FROM parcels WHERE pin = '14210010020000'"
    ).fetchone()
    assert row["building_sf"] == 3000
    assert row["building_sf_source"] == "assessor"
    assert row["year_built"] == 1958
    assert row["unit_count"] == 8


@responses.activate
def test_assessor_wins_with_post_2015_construction(db_path, geo, cook_client, cdp_client):
    """A new (post-2015) building has assessor data and no matching footprint;
    even if a stale footprint hits the parcel spatially, assessor stays in
    place because of the universal 'assessor wins' rule."""
    _seed(geo, db_path, cook_client)

    conn = get_connection(db_path)
    conn.execute(
        "UPDATE parcels SET year_built=2020, building_sf=999, condition='Excellent', unit_count=1 "
        "WHERE pin='14210010010000'"
    )
    conn.commit()
    conn.close()

    _run_footprints(geo, db_path, cdp_client)

    conn = get_connection(db_path)
    row = conn.execute(
        "SELECT year_built, building_sf, condition, unit_count, "
        "       building_sf_source, condition_source "
        "FROM parcels WHERE pin = '14210010010000'"
    ).fetchone()
    assert row["year_built"] == 2020
    assert row["building_sf"] == 999
    assert row["condition"] == "Excellent"
    assert row["unit_count"] == 1


@responses.activate
def test_footprint_translates_condition_when_assessor_null(db_path, geo, cook_client, cdp_client):
    _seed(geo, db_path, cook_client)
    # Parcel 30000's assessor characteristics give it condition='Average'.
    # Null it out so the footprint translation path runs.
    conn = get_connection(db_path)
    conn.execute("UPDATE parcels SET condition=NULL WHERE pin='14210010030000'")
    conn.commit()
    conn.close()

    _run_footprints(geo, db_path, cdp_client)

    conn = get_connection(db_path)
    # FP-MAIN-30 has bldg_condi='SOUND' → translate to 'Average' per the map.
    row = conn.execute(
        "SELECT condition, condition_source FROM parcels WHERE pin = '14210010030000'"
    ).fetchone()
    assert row["condition"] == "Average"
    assert row["condition_source"] == "footprint"


@responses.activate
def test_raw_footprints_persisted(db_path, geo, cook_client, cdp_client):
    _seed(geo, db_path, cook_client)
    _run_footprints(geo, db_path, cdp_client)

    conn = get_connection(db_path)
    rows = conn.execute("SELECT bldg_id FROM raw_cdp_building_footprints").fetchall()
    ids = sorted(r["bldg_id"] for r in rows)
    # Demolished excluded; the four ACTIVE rows persisted.
    assert ids == ["FP-GARAGE-10", "FP-MAIN-10", "FP-MAIN-20", "FP-MAIN-30"]
