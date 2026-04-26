"""Source 1D — Cook County Assessor Assessed Values."""
from __future__ import annotations
from datetime import datetime, UTC
from pathlib import Path
from collections import defaultdict
from pipeline.config import GeographyConfig, CONFIG_DIR
from pipeline.db import upsert_rows, get_connection
from pipeline.socrata import SocrataClient
from pipeline.tax import estimate_annual_tax, load_tax_constants


DATASET_ID = "uzyt-m557"
TABLE = "raw_assessor_values"
SOURCE_NAME = "assessor_values"


def _f(v):
    if v in (None, ""): return None
    try: return float(v)
    except (TypeError, ValueError): return None


def fetch(geo: GeographyConfig, db_path: Path, client: SocrataClient) -> int:
    fetched_at = datetime.now(UTC).isoformat(timespec="seconds")
    conn = get_connection(db_path)
    try:
        known_pins = {row[0] for row in conn.execute("SELECT pin FROM parcels")}
    finally:
        conn.close()
    if not known_pins:
        return 0

    raw_rows = []
    for r in client.fetch_by_pins(DATASET_ID, known_pins, order="year DESC"):
        pin = r.get("pin")
        if pin not in known_pins:
            continue
        raw_rows.append({
            "pin": pin, "year": r.get("year"),
            "mailed_bldg": _f(r.get("mailed_bldg")),
            "mailed_land": _f(r.get("mailed_land")),
            "mailed_tot": _f(r.get("mailed_tot")),
            "certified_bldg": _f(r.get("certified_bldg")),
            "certified_land": _f(r.get("certified_land")),
            "certified_tot": _f(r.get("certified_tot")),
            "board_bldg": _f(r.get("board_bldg")),
            "board_land": _f(r.get("board_land")),
            "board_tot": _f(r.get("board_tot")),
            "board_hie": _f(r.get("board_hie")),
            "fetched_at": fetched_at,
        })

    n = upsert_rows(db_path, TABLE, raw_rows, key_columns=["pin", "year"])

    # Group by PIN, sort by year DESC, compute trends
    by_pin: dict[str, list[dict]] = defaultdict(list)
    for r in raw_rows:
        by_pin[r["pin"]].append(r)
    for rows in by_pin.values():
        rows.sort(key=lambda x: int(x["year"]) if x["year"] else 0, reverse=True)

    def _pick(row):
        # board → certified → mailed precedence; the latest tax year typically
        # has board_tot still NULL until BOR publishes. Fall back per row.
        if row["board_tot"] is not None:
            return row["board_tot"], row["board_land"], row["board_bldg"]
        if row["certified_tot"] is not None:
            return row["certified_tot"], row["certified_land"], row["certified_bldg"]
        if row["mailed_tot"] is not None:
            return row["mailed_tot"], row["mailed_land"], row["mailed_bldg"]
        return None, None, None

    tax_constants = load_tax_constants(CONFIG_DIR / "tax_constants.yaml")

    conn = get_connection(db_path)
    try:
        for pin, rows in by_pin.items():
            current = next((r for r in rows if _pick(r)[0] is not None), None)
            if current is None:
                continue
            assessed_total, assessed_land, assessed_bldg = _pick(current)
            ratio = (assessed_land / assessed_total) if (assessed_land and assessed_total) else None

            current_year = int(current["year"]) if current["year"] else None
            inc_1yr = None
            if current_year is not None:
                prior = next(
                    (r for r in rows
                     if r["year"] and int(r["year"]) < current_year
                     and _pick(r)[0] is not None),
                    None,
                )
                if prior:
                    prior_tot, _, _ = _pick(prior)
                    inc_1yr = (assessed_total / prior_tot - 1) * 100

            inc_5yr = None
            if current_year is not None:
                target_year = current_year - 5
                old = next(
                    (r for r in rows
                     if r["year"] and int(r["year"]) <= target_year
                     and _pick(r)[0] is not None),
                    None,
                )
                if old:
                    old_tot, _, _ = _pick(old)
                    inc_5yr = (assessed_total / old_tot - 1) * 100

            # Estimated annual tax. Homeowner exemption is left off until we
            # ingest a per-parcel exemptions feed; raw_assessor_exempt covers
            # only fully-tax-exempt parcels (churches, schools), not the
            # standard $10K-EAV homeowner reduction.
            est_tax = estimate_annual_tax(
                assessed_total=assessed_total,
                equalizer=tax_constants["equalizer"],
                composite_rate_pct=tax_constants["composite_rate_pct"],
                homeowner_exemption_eav_reduction=tax_constants["homeowner_exemption_eav_reduction"],
                has_homeowner_exemption=False,
            )

            conn.execute("""
                UPDATE parcels SET
                    assessed_land = :al,
                    assessed_building = :ab,
                    assessed_total = :at,
                    land_building_ratio = :ratio,
                    estimated_annual_tax = :etax,
                    tax_increase_pct_1yr = :i1,
                    tax_increase_pct_5yr = :i5,
                    last_updated_date = :now
                WHERE pin = :pin
            """, {"al": assessed_land, "ab": assessed_bldg, "at": assessed_total,
                  "ratio": ratio, "etax": est_tax,
                  "i1": inc_1yr, "i5": inc_5yr,
                  "now": fetched_at, "pin": pin})
        conn.commit()
    finally:
        conn.close()
    return n
