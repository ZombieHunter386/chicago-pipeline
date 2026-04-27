"""Pipeline orchestrator: run every data source against the target geography."""
from __future__ import annotations
import argparse
import os
import time
from dataclasses import dataclass
from datetime import datetime, UTC
from pathlib import Path
from typing import Callable, Optional

from dotenv import load_dotenv

from pipeline.config import CONFIG_DIR, GeographyConfig, get_geography
from pipeline.db import init_db, get_connection
from pipeline.socrata import SocrataClient
from pipeline.consolidate import consolidate
from pipeline.condo_rollup import rollup_condos

from sources import (
    assessor_parcels, assessor_addresses, assessor_characteristics,
    assessor_values, assessor_sales, assessor_appeals, assessor_exempt,
    cdp_zoning, cdp_permits, cdp_violations, cdp_vacant, cdp_cta_stations,
    clerk_delinquent,
)


COOK_DOMAIN = "datacatalog.cookcountyil.gov"
CDP_DOMAIN = "data.cityofchicago.org"


@dataclass
class SourceResult:
    source_name: str
    status: str            # 'ok' | 'error'
    rows_fetched: int
    duration_s: float
    error_message: Optional[str] = None


def _log_start(db_path: Path, source: str) -> int:
    conn = get_connection(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO fetch_log (source_name, started_at, status) VALUES (?, ?, 'running')",
            (source, datetime.now(UTC).isoformat(timespec="seconds")),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _log_finish(db_path: Path, log_id: int, status: str, rows: int, err: str | None):
    conn = get_connection(db_path)
    try:
        conn.execute("""
            UPDATE fetch_log SET finished_at = ?, rows_fetched = ?, status = ?, error_message = ?
            WHERE log_id = ?
        """, (datetime.now(UTC).isoformat(timespec="seconds"), rows, status, err, log_id))
        conn.commit()
    finally:
        conn.close()


def _run(source_name: str, fn: Callable, db_path: Path, *args) -> SourceResult:
    log_id = _log_start(db_path, source_name)
    started = time.monotonic()
    try:
        rows = fn(*args)
        dur = time.monotonic() - started
        _log_finish(db_path, log_id, "ok", rows or 0, None)
        print(f"[{source_name}] ok — {rows} rows in {dur:.1f}s")
        return SourceResult(source_name, "ok", rows or 0, dur)
    except Exception as e:
        dur = time.monotonic() - started
        _log_finish(db_path, log_id, "error", 0, str(e))
        print(f"[{source_name}] ERROR: {e}")
        return SourceResult(source_name, "error", 0, dur, str(e))


def run_all(geo: GeographyConfig, db_path: Path, app_token: str,
            api_key_id: str = "", api_key_secret: str = "") -> list[SourceResult]:
    cook = SocrataClient(domain=COOK_DOMAIN, app_token=app_token,
                         api_key_id=api_key_id, api_key_secret=api_key_secret,
                         rate_limit_sleep=0.1)
    cdp = SocrataClient(domain=CDP_DOMAIN, app_token=app_token,
                        api_key_id=api_key_id, api_key_secret=api_key_secret,
                        rate_limit_sleep=0.1)

    results: list[SourceResult] = []
    # Order matters — parcels first, then joining sources
    results.append(_run("assessor_parcels", assessor_parcels.fetch, db_path, geo, db_path, cook))
    results.append(_run("assessor_addresses", assessor_addresses.fetch, db_path, geo, db_path, cook))
    results.append(_run("assessor_characteristics", assessor_characteristics.fetch, db_path, geo, db_path, cook))
    results.append(_run("assessor_values", assessor_values.fetch, db_path, geo, db_path, cook))
    results.append(_run("assessor_sales", assessor_sales.fetch, db_path, geo, db_path, cook))
    results.append(_run("assessor_appeals", assessor_appeals.fetch, db_path, geo, db_path, cook))
    results.append(_run("assessor_exempt", assessor_exempt.fetch, db_path, geo, db_path, cook))
    results.append(_run("cdp_zoning", cdp_zoning.fetch, db_path, geo, db_path, cdp))
    results.append(_run("cdp_permits", cdp_permits.fetch, db_path, geo, db_path, cdp))
    results.append(_run("cdp_violations", cdp_violations.fetch, db_path, geo, db_path, cdp))
    results.append(_run("cdp_vacant", cdp_vacant.fetch, db_path, geo, db_path, cdp))
    results.append(_run("cdp_cta_stations", cdp_cta_stations.fetch, db_path, geo, db_path, cdp))
    results.append(_run("clerk_delinquent", clerk_delinquent.fetch, db_path, geo, db_path, None))
    results.append(_run("consolidate", consolidate, db_path, db_path))
    results.append(_run("condo_rollup", rollup_condos, db_path, db_path))
    return results


def main():
    parser = argparse.ArgumentParser(description="Run all data fetches for the Chicago multifamily pipeline.")
    parser.add_argument("--db", default=None, help="Override DB path")
    parser.add_argument("--config-dir", default=None, help="Override config dir")
    args = parser.parse_args()

    load_dotenv()
    app_token = os.environ.get("SOCRATA_APP_TOKEN", "")
    api_key_id = os.environ.get("SOCRATA_API_KEY_ID", "")
    api_key_secret = os.environ.get("SOCRATA_API_KEY_SECRET", "")
    if not app_token and not (api_key_id and api_key_secret):
        print("WARNING: no Socrata credentials set — requests will be rate-limited.")

    config_dir = Path(args.config_dir) if args.config_dir else CONFIG_DIR
    db_path = Path(args.db) if args.db else Path(os.environ.get("PIPELINE_DB_PATH", "data/pipeline.db"))

    init_db(db_path)
    geo = get_geography(config_dir)

    results = run_all(geo, db_path, app_token, api_key_id=api_key_id, api_key_secret=api_key_secret)

    print("\n=== Summary ===")
    for r in results:
        print(f"  {r.source_name:30s} {r.status:5s} {r.rows_fetched:>8d} rows  {r.duration_s:.1f}s")
    fails = [r for r in results if r.status != "ok"]
    if fails:
        print(f"\n{len(fails)} sources failed.")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
