import json
import responses
import pytest
from datetime import date
from pipeline import fetch_all
from pipeline.db import get_connection
from tests.conftest import FIXTURES


def _register_all(monkeypatch):
    from sources import (
        assessor_parcels, assessor_addresses, assessor_characteristics,
        assessor_values, assessor_sales, assessor_appeals, assessor_exempt,
        cdp_zoning, cdp_permits, cdp_violations, cdp_vacant, cdp_cta_stations,
    )
    from sources.assessor_sales import TODAY as _    # just to ensure import
    monkeypatch.setattr(cdp_permits, "TODAY", date(2026, 4, 19))
    monkeypatch.setattr(cdp_violations, "TODAY", date(2026, 4, 19))

    cc = "https://datacatalog.cookcountyil.gov/resource"
    cdp = "https://data.cityofchicago.org/resource"
    for ds, fname in [
        (assessor_parcels.DATASET_ID, "assessor_parcels.json"),
        (assessor_addresses.DATASET_ID, "assessor_addresses.json"),
        (assessor_characteristics.DATASET_ID, "assessor_characteristics.json"),
        (assessor_values.DATASET_ID, "assessor_values.json"),
        (assessor_sales.DATASET_ID, "assessor_sales.json"),
        (assessor_appeals.DATASET_ID, "assessor_appeals.json"),
        (assessor_exempt.DATASET_ID, "assessor_exempt.json"),
    ]:
        fx = json.loads((FIXTURES / fname).read_text())
        responses.add(responses.GET, f"{cc}/{ds}.json", json=fx, status=200)
        responses.add(responses.GET, f"{cc}/{ds}.json", json=[], status=200)
    for ds, fname in [
        (cdp_zoning.DATASET_ID, "cdp_zoning.json"),
        (cdp_permits.DATASET_ID, "cdp_permits.json"),
        (cdp_violations.DATASET_ID, "cdp_violations.json"),
        (cdp_vacant.DATASET_ID, "cdp_vacant.json"),
        (cdp_cta_stations.DATASET_ID, "cdp_cta_stations.json"),
    ]:
        fx = json.loads((FIXTURES / fname).read_text())
        responses.add(responses.GET, f"{cdp}/{ds}.json", json=fx, status=200)
        responses.add(responses.GET, f"{cdp}/{ds}.json", json=[], status=200)


@responses.activate
def test_run_all_populates_db_and_logs_each_source(tmp_path, monkeypatch, geo):
    db = tmp_path / "pipeline.db"
    from pipeline.db import init_db
    init_db(db)

    _register_all(monkeypatch)
    # Point clerk CSV at fixture
    monkeypatch.setattr("sources.clerk_delinquent.DEFAULT_CSV_PATH", FIXTURES / "delinquent.csv")

    results = fetch_all.run_all(geo, db, app_token="TKN")

    # Every source has a result, no failures
    assert all(r.status == "ok" for r in results), [r for r in results if r.status != "ok"]
    # Parcels table has rows from the fixture
    conn = get_connection(db)
    n = conn.execute("SELECT COUNT(*) FROM parcels").fetchone()[0]
    assert n >= 2
    # fetch_log rows exist for each source
    sources_logged = {r[0] for r in conn.execute("SELECT source_name FROM fetch_log").fetchall()}
    for expected in ("assessor_parcels", "cdp_zoning", "cdp_cta_stations",
                     "clerk_delinquent", "consolidate"):
        assert expected in sources_logged


@responses.activate
def test_run_all_continues_after_single_source_error(tmp_path, monkeypatch, geo):
    db = tmp_path / "pipeline.db"
    from pipeline.db import init_db
    init_db(db)
    _register_all(monkeypatch)
    monkeypatch.setattr("sources.clerk_delinquent.DEFAULT_CSV_PATH", FIXTURES / "delinquent.csv")

    # Force the appeals module to raise
    from sources import assessor_appeals
    def boom(*a, **kw): raise RuntimeError("boom")
    monkeypatch.setattr(assessor_appeals, "fetch", boom)

    results = fetch_all.run_all(geo, db, app_token="TKN")
    errs = [r for r in results if r.status == "error"]
    oks = [r for r in results if r.status == "ok"]
    assert len(errs) == 1
    assert errs[0].source_name == "assessor_appeals"
    assert len(oks) > 0
