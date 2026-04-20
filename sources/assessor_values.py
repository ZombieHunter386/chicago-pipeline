"""Source 1D — Cook County Assessor Assessed Values."""
from __future__ import annotations
from datetime import datetime, UTC
from pathlib import Path
from collections import defaultdict
from pipeline.config import GeographyConfig
from pipeline.db import upsert_rows, get_connection
from pipeline.socrata import SocrataClient


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
    for r in client.fetch(DATASET_ID, order="year DESC"):
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

    conn = get_connection(db_path)
    try:
        for pin, rows in by_pin.items():
            current = rows[0]
            assessed_total = current["board_tot"]
            assessed_land = current["board_land"]
            assessed_bldg = current["board_bldg"]
            ratio = (assessed_land / assessed_total) if (assessed_land and assessed_total) else None

            inc_1yr = None
            if len(rows) >= 2 and rows[1]["board_tot"] and rows[0]["board_tot"]:
                inc_1yr = (rows[0]["board_tot"] / rows[1]["board_tot"] - 1) * 100

            inc_5yr = None
            current_year = int(current["year"]) if current["year"] else None
            if current_year is not None:
                target_year = current_year - 5
                old = next((r for r in rows if r["year"] and int(r["year"]) <= target_year), None)
                if old and old["board_tot"] and current["board_tot"]:
                    inc_5yr = (current["board_tot"] / old["board_tot"] - 1) * 100

            conn.execute("""
                UPDATE parcels SET
                    assessed_land = :al,
                    assessed_building = :ab,
                    assessed_total = :at,
                    land_building_ratio = :ratio,
                    tax_increase_pct_1yr = :i1,
                    tax_increase_pct_5yr = :i5,
                    last_updated_date = :now
                WHERE pin = :pin
            """, {"al": assessed_land, "ab": assessed_bldg, "at": assessed_total,
                  "ratio": ratio, "i1": inc_1yr, "i5": inc_5yr,
                  "now": fetched_at, "pin": pin})
        conn.commit()
    finally:
        conn.close()
    return n
