"""Source 1G — Cook County Assessor Tax-Exempt Parcels."""
from __future__ import annotations
from datetime import datetime, UTC
from pathlib import Path
from pipeline.config import GeographyConfig
from pipeline.db import upsert_rows, get_connection
from pipeline.socrata import SocrataClient


DATASET_ID = "vgzx-68gb"
TABLE = "raw_assessor_exempt"
SOURCE_NAME = "assessor_exempt"


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
    for r in client.fetch(DATASET_ID):
        pin = r.get("pin")
        if pin not in known_pins:
            continue
        raw_rows.append({
            "pin": pin,
            "exemption_type": r.get("exemption_type") or r.get("exempt_type"),
            "fetched_at": fetched_at,
        })
    return upsert_rows(db_path, TABLE, raw_rows, key_columns=["pin"])
