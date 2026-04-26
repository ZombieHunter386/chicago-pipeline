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


SUFFIX_MAP = {
    "AVENUE": "AVE", "AV": "AVE", "AVE": "AVE",
    "BOULEVARD": "BLVD", "BL": "BLVD", "BLVD": "BLVD",
    "PARKWAY": "PKWY", "PKY": "PKWY", "PKWY": "PKWY",
    "STREET": "ST", "ST": "ST",
    "ROAD": "RD", "RD": "RD",
    "DRIVE": "DR", "DR": "DR",
    "LANE": "LN", "LN": "LN",
    "COURT": "CT", "CT": "CT",
    "PLACE": "PL", "PL": "PL",
    "TERRACE": "TER", "TER": "TER",
    "HIGHWAY": "HWY", "HWY": "HWY",
    "PLAZA": "PLZ", "PLZ": "PLZ",
    "SQUARE": "SQ", "SQ": "SQ",
    "WAY": "WAY",
}
DIRECTION_MAP = {
    "NORTH": "N", "N": "N",
    "SOUTH": "S", "S": "S",
    "EAST": "E", "E": "E",
    "WEST": "W", "W": "W",
    "NORTHEAST": "NE", "NE": "NE",
    "NORTHWEST": "NW", "NW": "NW",
    "SOUTHEAST": "SE", "SE": "SE",
    "SOUTHWEST": "SW", "SW": "SW",
}
SUFFIX_TOKENS = set(SUFFIX_MAP.values())
UNIT_MARKER_RE = re.compile(r"\b(UNIT|APT|APARTMENT|STE|SUITE)\s+\S+", re.IGNORECASE)
HASH_UNIT_RE = re.compile(r"#\s*\S+")


def _street_key(addr: str | None) -> str | None:
    """Canonicalize an address to '<number> <dir> <street...> <suffix>'.
    Drops unit numbers (UNIT 5, APT 3, #4, trailing 1E/2W condo tokens).
    Returns None for empty input. PO BOX-style addresses round-trip without
    a suffix and still compare correctly against street-form mail addresses."""
    if not addr:
        return None
    s = re.sub(r"\s+", " ", addr).strip().upper()
    # Strip explicit unit markers first so they don't muddy tokenization.
    s = UNIT_MARKER_RE.sub(" ", s)
    s = HASH_UNIT_RE.sub(" ", s)
    # Insert space at letter→digit and digit→letter boundaries so PKWY1E → PKWY 1 E
    s = re.sub(r"([A-Z])(\d)", r"\1 \2", s)
    s = re.sub(r"(\d)([A-Z])", r"\1 \2", s)
    s = re.sub(r"[.,]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    if not s:
        return None
    tokens = [DIRECTION_MAP.get(t, SUFFIX_MAP.get(t, t)) for t in s.split()]
    # Truncate after the last suffix token so trailing unit fragments are dropped.
    last_suffix = -1
    for i, t in enumerate(tokens):
        if t in SUFFIX_TOKENS:
            last_suffix = i
    if last_suffix >= 0:
        tokens = tokens[:last_suffix + 1]
    return " ".join(tokens) or None


def is_absentee(prop_addr: str | None, mail_addr: str | None) -> bool:
    p = _street_key(prop_addr)
    m = _street_key(mail_addr)
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
