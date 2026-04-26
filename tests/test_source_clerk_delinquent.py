import json
import responses
from sources import assessor_parcels, clerk_delinquent
from pipeline.db import get_connection
from tests.conftest import FIXTURES


def _seed_parcels(db_path, geo, cook_client):
    pf = json.loads((FIXTURES / "assessor_parcels.json").read_text())
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_parcels.DATASET_ID}.json",
        json=pf, status=200)
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_parcels.DATASET_ID}.json",
        json=[], status=200)
    assessor_parcels.fetch(geo, db_path, cook_client)


@responses.activate
def test_delinquent_csv_flags_pins(db_path, geo, cook_client):
    pf = json.loads((FIXTURES / "assessor_parcels.json").read_text())
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_parcels.DATASET_ID}.json",
        json=pf, status=200)
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_parcels.DATASET_ID}.json",
        json=[], status=200)
    assessor_parcels.fetch(geo, db_path, cook_client)

    clerk_delinquent.fetch_from_csv(FIXTURES / "delinquent.csv", db_path)

    conn = get_connection(db_path)
    p1 = conn.execute("SELECT tax_delinquent, delinquency_years FROM parcels WHERE pin='14210010010000'").fetchone()
    assert p1["tax_delinquent"] == 1
    assert p1["delinquency_years"] == 3   # 2022, 2023, 2024
    p2 = conn.execute("SELECT tax_delinquent, delinquency_years FROM parcels WHERE pin='14210010020000'").fetchone()
    assert p2["tax_delinquent"] == 1
    assert p2["delinquency_years"] == 1

    # unknown pin row filtered out
    raw = conn.execute("SELECT pin FROM raw_clerk_delinquent").fetchall()
    pins = {r[0] for r in raw}
    assert "99999990000000" not in pins


@responses.activate
def test_delinquent_csv_handles_utf8_bom(db_path, geo, cook_client, tmp_path):
    """Excel-exported CSVs start with a UTF-8 BOM; DictReader must not be
    tripped up by it (else r.get('pin') returns None silently)."""
    _seed_parcels(db_path, geo, cook_client)
    csv_path = tmp_path / "bom.csv"
    csv_path.write_bytes(
        b"\xef\xbb\xbfpin,tax_year,amount_owed\n"
        b"14-21-001-001-0000,2024,500.00\n"
    )
    clerk_delinquent.fetch_from_csv(csv_path, db_path)
    conn = get_connection(db_path)
    p = conn.execute("SELECT tax_delinquent, delinquency_years FROM parcels WHERE pin='14210010010000'").fetchone()
    assert p["tax_delinquent"] == 1
    assert p["delinquency_years"] == 1


@responses.activate
def test_delinquent_csv_handles_currency_formatting(db_path, geo, cook_client, tmp_path):
    """Real exports often include '$' and thousands separators."""
    _seed_parcels(db_path, geo, cook_client)
    csv_path = tmp_path / "currency.csv"
    csv_path.write_text(
        "pin,tax_year,amount_owed\n"
        "14-21-001-001-0000,2023,\"$4,820.10\"\n"
        "14-21-001-001-0000,2024,\"5,498.30\"\n"
    )
    clerk_delinquent.fetch_from_csv(csv_path, db_path)
    conn = get_connection(db_path)
    r = conn.execute("SELECT total_owed, delinquent_years FROM raw_clerk_delinquent WHERE pin='14210010010000'").fetchone()
    assert r["delinquent_years"] == 2
    assert abs(r["total_owed"] - (4820.10 + 5498.30)) < 0.01


def test_clerk_delinquent_raises_when_csv_missing(db_path, tmp_path):
    """Missing CSV must raise — silently skipping leaves every parcel NULL on
    tax_delinquent and produces a fetch run that looks successful but has no
    delinquency data at all."""
    import pytest
    missing = tmp_path / "does_not_exist.csv"
    with pytest.raises(FileNotFoundError, match="delinquent CSV"):
        clerk_delinquent.fetch_from_csv(missing, db_path)


@responses.activate
def test_delinquent_rerun_clears_stale_flag(db_path, geo, cook_client, tmp_path):
    """A PIN flagged in a prior run that pays off its taxes must be cleared
    on the next run — not left monotonically flagged forever."""
    _seed_parcels(db_path, geo, cook_client)
    # First run: both pins delinquent
    clerk_delinquent.fetch_from_csv(FIXTURES / "delinquent.csv", db_path)
    conn = get_connection(db_path)
    before = conn.execute("SELECT tax_delinquent FROM parcels WHERE pin='14210010020000'").fetchone()
    assert before["tax_delinquent"] == 1
    conn.close()

    # Second run: only first pin still delinquent
    csv_path = tmp_path / "updated.csv"
    csv_path.write_text(
        "pin,tax_year,amount_owed\n"
        "14-21-001-001-0000,2024,5000.00\n"
    )
    clerk_delinquent.fetch_from_csv(csv_path, db_path)
    conn = get_connection(db_path)
    cleared = conn.execute("SELECT tax_delinquent, delinquency_years FROM parcels WHERE pin='14210010020000'").fetchone()
    still = conn.execute("SELECT tax_delinquent, delinquency_years FROM parcels WHERE pin='14210010010000'").fetchone()
    assert cleared["tax_delinquent"] == 0
    assert cleared["delinquency_years"] == 0
    assert still["tax_delinquent"] == 1
    assert still["delinquency_years"] == 1
