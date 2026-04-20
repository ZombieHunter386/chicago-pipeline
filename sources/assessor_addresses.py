"""Source 1B — Cook County Assessor Parcel Addresses."""
from __future__ import annotations
import re
from datetime import datetime, UTC
from pathlib import Path
from pipeline.config import GeographyConfig
from pipeline.db import upsert_rows, get_connection
from pipeline.socrata import SocrataClient


DATASET_ID = "3723-97qp"
TABLE = "raw_assessor_addresses"
SOURCE_NAME = "assessor_addresses"

# Keyword list uses no trailing dots inside alternatives; we allow an optional
# trailing `.` after the end `\b` so dotted forms like "L.L.C.", "L.P.", "Inc."
# still match. Using `\b` with a trailing `.` was the bug — `\b` requires a
# word char on one side, and a trailing `.` after the final letter makes that
# side a non-word character, so the boundary fails.
LLC_PATTERN = re.compile(
    r"\b(LLC|L\.L\.C|CORP|CORPORATION|INC|INCORPORATED|TRUST|LP|L\.P|PARTNERS|PARTNERSHIP|LLP|L\.L\.P|HOLDINGS|REALTY|PROPERTIES)\b\.?",
    re.IGNORECASE,
)


def is_llc(name: str | None) -> bool:
    if not name:
        return False
    return bool(LLC_PATTERN.search(name))


def _norm_addr(a: str | None) -> str | None:
    if not a:
        return None
    return re.sub(r"\s+", " ", a).strip().upper()


def is_absentee(prop_addr: str | None, mail_addr: str | None) -> bool:
    p = _norm_addr(prop_addr)
    m = _norm_addr(mail_addr)
    if p is None or m is None:
        return False
    return p != m


def fetch(geo: GeographyConfig, db_path: Path, client: SocrataClient) -> int:
    """
    No bbox prefilter possible — this dataset has no lat/lng. Filter to
    PINs already in our parcels table (set by Source 1A).
    """
    fetched_at = datetime.now(UTC).isoformat(timespec="seconds")
    conn = get_connection(db_path)
    try:
        known_pins = {row[0] for row in conn.execute("SELECT pin FROM parcels")}
    finally:
        conn.close()
    if not known_pins:
        return 0

    raw_rows = []
    for r in client.fetch_by_pins(DATASET_ID, known_pins):
        pin = r.get("pin")
        if pin not in known_pins:
            continue
        raw_rows.append({
            "pin": pin,
            "prop_address_full": r.get("prop_address_full"),
            "prop_address_city_name": r.get("prop_address_city_name"),
            "prop_address_state": r.get("prop_address_state"),
            "prop_address_zipcode_1": r.get("prop_address_zipcode_1"),
            "mail_address_name": r.get("mail_address_name"),
            "mail_address_full": r.get("mail_address_full"),
            "mail_address_city_name": r.get("mail_address_city_name"),
            "mail_address_state": r.get("mail_address_state"),
            "mail_address_zipcode_1": r.get("mail_address_zipcode_1"),
            "owner_address_name": r.get("owner_address_name"),
            "owner_address_full": r.get("owner_address_full"),
            "fetched_at": fetched_at,
        })

    n = upsert_rows(db_path, TABLE, raw_rows, key_columns=["pin"])

    # Update parcels with derived fields
    conn = get_connection(db_path)
    try:
        for r in raw_rows:
            owner = r["owner_address_name"] or r["mail_address_name"]
            absentee = 1 if is_absentee(r["prop_address_full"], r["mail_address_full"]) else 0
            llc = 1 if is_llc(r["mail_address_name"]) else 0
            conn.execute("""
                UPDATE parcels SET
                    address = :address,
                    owner_name = :owner_name,
                    owner_address = :owner_address,
                    mail_name = :mail_name,
                    mail_address = :mail_address,
                    is_absentee = :is_absentee,
                    is_llc = :is_llc,
                    last_updated_date = :now
                WHERE pin = :pin
            """, {
                "address": r["prop_address_full"],
                "owner_name": owner,
                "owner_address": r["owner_address_full"],
                "mail_name": r["mail_address_name"],
                "mail_address": r["mail_address_full"],
                "is_absentee": absentee,
                "is_llc": llc,
                "now": fetched_at,
                "pin": r["pin"],
            })
        conn.commit()
    finally:
        conn.close()
    return n
