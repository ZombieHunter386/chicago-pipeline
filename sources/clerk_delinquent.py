"""Source 3A — Cook County Clerk Delinquent Property Tax (bulk CSV)."""
from __future__ import annotations
import csv
from datetime import datetime, UTC
from pathlib import Path
from collections import defaultdict
from pipeline.db import upsert_rows, get_connection


SOURCE_NAME = "clerk_delinquent"
DEFAULT_CSV_PATH = Path("data/delinquent.csv")


def _normalize_pin(raw: str) -> str:
    """Clerk CSV uses dashed PINs (14-21-001-001-0000); normalize to 14-digit."""
    return (raw or "").replace("-", "").strip()


def _parse_amount(raw) -> float | None:
    """Parse currency-formatted amounts: '$4,820.10', '4,820.10', '4820.10'."""
    if raw in (None, ""):
        return None
    s = str(raw).replace("$", "").replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def fetch_from_csv(csv_path: Path, db_path: Path) -> int:
    fetched_at = datetime.now(UTC).isoformat(timespec="seconds")
    if not csv_path.exists():
        print(f"Delinquent CSV not found at {csv_path} — skipping")
        return 0

    conn = get_connection(db_path)
    try:
        known_pins = {row[0] for row in conn.execute("SELECT pin FROM parcels")}
    finally:
        conn.close()

    by_pin: dict[str, list[dict]] = defaultdict(list)
    # encoding="utf-8-sig" strips the BOM that Excel-exported CSVs often start with.
    with csv_path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for r in reader:
            pin = _normalize_pin(r.get("pin") or "")
            if pin not in known_pins:
                continue
            by_pin[pin].append(r)

    raw_rows = []
    for pin, rows in by_pin.items():
        years = sorted({int(r["tax_year"]) for r in rows if r.get("tax_year")})
        total = sum(a for a in (_parse_amount(r.get("amount_owed")) for r in rows) if a is not None)
        raw_rows.append({
            "pin": pin,
            "delinquent_years": len(years),
            "earliest_delinquent_year": years[0] if years else None,
            "total_owed": total,
            "fetched_at": fetched_at,
        })

    n = upsert_rows(db_path, "raw_clerk_delinquent", raw_rows, key_columns=["pin"])

    current_pins = {r["pin"] for r in raw_rows}
    conn = get_connection(db_path)
    try:
        # Clear stale flags on PINs that were previously delinquent but no longer
        # appear in the current CSV (taxes paid off). Restricted to PINs we've
        # touched before (tax_delinquent = 1) to avoid writing 0s everywhere.
        if current_pins:
            placeholders = ",".join("?" * len(current_pins))
            conn.execute(
                f"""UPDATE parcels SET
                        tax_delinquent = 0,
                        delinquency_years = 0,
                        last_updated_date = ?
                    WHERE tax_delinquent = 1 AND pin NOT IN ({placeholders})""",
                (fetched_at, *current_pins),
            )
        else:
            conn.execute(
                "UPDATE parcels SET tax_delinquent=0, delinquency_years=0, last_updated_date=? "
                "WHERE tax_delinquent = 1",
                (fetched_at,),
            )
        for r in raw_rows:
            conn.execute("""
                UPDATE parcels SET
                    tax_delinquent = 1,
                    delinquency_years = :y,
                    last_updated_date = :t
                WHERE pin = :pin
            """, {"y": r["delinquent_years"], "t": fetched_at, "pin": r["pin"]})
        conn.commit()
    finally:
        conn.close()
    return n


def fetch(geo, db_path: Path, client=None) -> int:
    """Standard fetch interface — reads from DEFAULT_CSV_PATH."""
    return fetch_from_csv(DEFAULT_CSV_PATH, db_path)
