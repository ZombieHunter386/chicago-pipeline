"""Source 1B — Cook County Assessor Parcel Addresses."""
from __future__ import annotations
from datetime import datetime, UTC
from pathlib import Path
from pipeline.config import GeographyConfig
from pipeline.db import upsert_rows, get_connection
from pipeline.socrata import SocrataClient

# Address normalization helpers live in pipeline/address.py; re-exported here
# for backwards compatibility with existing imports/tests.
from pipeline.address import (
    is_llc,
    is_absentee,
    street_key as _street_key,
    LLC_PATTERN,
    SUFFIX_MAP,
    DIRECTION_MAP,
)

__all__ = [
    "DATASET_ID", "TABLE", "SOURCE_NAME",
    "fetch", "is_llc", "is_absentee",
    "_street_key", "LLC_PATTERN", "SUFFIX_MAP", "DIRECTION_MAP",
]


DATASET_ID = "3723-97qp"
TABLE = "raw_assessor_addresses"
SOURCE_NAME = "assessor_addresses"


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
            llc = 1 if (is_llc(r["owner_address_name"]) or is_llc(r["mail_address_name"])) else 0
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
