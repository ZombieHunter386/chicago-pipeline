from __future__ import annotations
import sqlite3
from pathlib import Path
from typing import Any
import yaml


def build_filter_schema(db_path: Path, config_path: Path) -> dict[str, Any]:
    """
    Load ui_filters.yaml and enrich each filter with live DB info:
      - range: min/max from SELECT MIN/MAX
      - dropdown: distinct non-null values from SELECT DISTINCT
      - checkbox: no enrichment
      - text_search: no enrichment
      - date_range: min/max date strings
    Output is JSON-serializable for direct return from the /api/filters route.
    """
    with config_path.open() as f:
        raw = yaml.safe_load(f) or {}

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        groups = []
        for group in raw.get("filter_groups", []):
            enriched_filters = []
            for f in group.get("filters", []):
                enriched_filters.append(_enrich_filter(conn, f))
            groups.append({"group": group["group"], "filters": enriched_filters})

        return {
            "filter_groups": groups,
            "stage_pills": raw.get("stage_pills", {}),
        }
    finally:
        conn.close()


def _enrich_filter(conn: sqlite3.Connection, f: dict) -> dict:
    col = f["column"]
    ftype = f["type"]
    out = {"column": col, "label": f["label"], "type": ftype}

    if ftype == "range":
        row = conn.execute(
            f"SELECT MIN({col}) AS mn, MAX({col}) AS mx FROM parcels "
            f"WHERE {col} IS NOT NULL"
        ).fetchone()
        out["min"] = row["mn"]
        out["max"] = row["mx"]

    elif ftype == "dropdown":
        rows = conn.execute(
            f"SELECT DISTINCT {col} AS v FROM parcels "
            f"WHERE {col} IS NOT NULL AND {col} != '' ORDER BY {col}"
        ).fetchall()
        out["options"] = [r["v"] for r in rows]

    elif ftype == "date_range":
        row = conn.execute(
            f"SELECT MIN({col}) AS mn, MAX({col}) AS mx FROM parcels "
            f"WHERE {col} IS NOT NULL AND {col} != ''"
        ).fetchone()
        out["min"] = row["mn"]
        out["max"] = row["mx"]

    # checkbox, text_search: nothing to enrich
    return out
