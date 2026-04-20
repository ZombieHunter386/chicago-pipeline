"""Source 1E — Cook County Assessor Parcel Sales."""
from __future__ import annotations
from datetime import datetime, date, UTC
from pathlib import Path
from collections import defaultdict
from pipeline.config import GeographyConfig
from pipeline.db import upsert_rows, get_connection
from pipeline.socrata import SocrataClient


DATASET_ID = "wvhk-k5uv"
TABLE = "raw_assessor_sales"
SOURCE_NAME = "assessor_sales"
TODAY = date.today()


def _f(v):
    if v in (None, ""): return None
    try: return float(v)
    except (TypeError, ValueError): return None

def _b(v):
    if v in (None, ""): return None
    return 1 if str(v).lower() in ("true", "t", "y", "yes", "1") else 0

def _date_only(v):
    if not v: return None
    return v[:10]


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
    for r in client.fetch(DATASET_ID, order="sale_date DESC"):
        pin = r.get("pin")
        if pin not in known_pins:
            continue
        raw_rows.append({
            "pin": pin,
            "sale_date": _date_only(r.get("sale_date")),
            "sale_price": _f(r.get("sale_price")),
            "seller_name": r.get("seller_name"),
            "buyer_name": r.get("buyer_name"),
            "deed_type": r.get("deed_type"),
            "doc_no": r.get("doc_no") or "",
            "is_multisale": _b(r.get("is_multisale")),
            "num_parcels_sale": int(float(r["num_parcels_sale"])) if r.get("num_parcels_sale") else None,
            "sale_filter_same_sale_within_365": _b(r.get("sale_filter_same_sale_within_365")),
            "sale_filter_less_than_10k": _b(r.get("sale_filter_less_than_10k")),
            "sale_filter_deed_type": _b(r.get("sale_filter_deed_type")),
            "fetched_at": fetched_at,
        })

    n = upsert_rows(db_path, TABLE, raw_rows, key_columns=["pin", "sale_date", "doc_no"])

    # Most recent arm's-length sale per PIN
    by_pin: dict[str, list[dict]] = defaultdict(list)
    for r in raw_rows:
        if r["sale_filter_same_sale_within_365"] or r["sale_filter_less_than_10k"]:
            continue
        if not r["sale_date"]:
            continue
        by_pin[r["pin"]].append(r)
    for v in by_pin.values():
        v.sort(key=lambda x: x["sale_date"], reverse=True)

    conn = get_connection(db_path)
    try:
        for pin, rows in by_pin.items():
            latest = rows[0]
            sd = datetime.strptime(latest["sale_date"], "%Y-%m-%d").date()
            hold = (TODAY - sd).days / 365.25
            conn.execute("""
                UPDATE parcels SET
                    last_sale_date = :sd,
                    last_sale_price = :sp,
                    hold_duration_years = :hold,
                    deed_type = :deed,
                    last_updated_date = :now
                WHERE pin = :pin
            """, {"sd": latest["sale_date"], "sp": latest["sale_price"],
                  "hold": round(hold, 2), "deed": latest["deed_type"],
                  "now": fetched_at, "pin": pin})
        conn.commit()
    finally:
        conn.close()
    return n
