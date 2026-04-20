"""Source 1F — Cook County Assessor Appeals."""
from __future__ import annotations
from datetime import datetime, UTC
from pathlib import Path
from collections import Counter
from pipeline.config import GeographyConfig
from pipeline.db import upsert_rows, get_connection
from pipeline.socrata import SocrataClient


DATASET_ID = "y282-6ig3"
TABLE = "raw_assessor_appeals"
SOURCE_NAME = "assessor_appeals"


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
    counts: Counter[str] = Counter()
    for r in client.fetch(DATASET_ID):
        pin = r.get("pin")
        if pin not in known_pins:
            continue
        raw_rows.append({
            "pin": pin, "year": r.get("year"),
            "appeal_outcome": r.get("appeal_outcome"),
            "assessed_value_change": _f(r.get("assessed_value_change")),
            "fetched_at": fetched_at,
        })
        counts[pin] += 1

    n = upsert_rows(db_path, TABLE, raw_rows, key_columns=["pin", "year"])

    conn = get_connection(db_path)
    try:
        for pin, c in counts.items():
            conn.execute("UPDATE parcels SET appeal_count=:c, last_updated_date=:t WHERE pin=:pin",
                         {"c": c, "t": fetched_at, "pin": pin})
        conn.commit()
    finally:
        conn.close()
    return n
