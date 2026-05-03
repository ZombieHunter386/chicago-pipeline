"""Targeted DB cleanup pass for known data-quality issues.

All corrections live in config/data_corrections.yaml — edit that file
(not this Python) to record new manual corrections. This module only
loads the YAML and applies it to the parcels table.

Idempotent: re-runs converge to the same end state. Run after fetch_all
and before consolidate / condo_rollup / score so downstream steps see
the cleaned values. The YAML-driven design means manual corrections
persist across re-fetches as long as cleanup runs after every fetch.

Three operations, all driven by config/data_corrections.yaml:
  1. NULL building_sf for PINs in `building_sf.set_null` — known-wrong
     values where true SF is unknown.
  2. Set building_sf = 0 for every parcel whose property_class is in
     `building_sf.set_zero_by_class` — confirmed as parking lots /
     no enclosed building.
  3. Derive is_low_util_land from `scoring.low_util_land_classes`.
     Replaces is_llc as the scoring pipeline's "underutilized site" signal.
"""
from __future__ import annotations
from pathlib import Path

import yaml

from pipeline.db import get_connection


_CORRECTIONS_PATH = Path(__file__).resolve().parent.parent / "config" / "data_corrections.yaml"


def load_corrections(path: Path = _CORRECTIONS_PATH) -> dict:
    """Load and lightly validate config/data_corrections.yaml. Returns a
    dict with three top-level keys: nullify_pins (list[str]),
    zero_classes (list[str]), low_util_classes (list[str])."""
    with path.open() as f:
        raw = yaml.safe_load(f) or {}
    bsf = raw.get("building_sf") or {}
    scoring = raw.get("scoring") or {}
    nullify = [str(entry["pin"]) for entry in (bsf.get("set_null") or [])
               if entry and "pin" in entry]
    zero_classes = [str(c) for c in (bsf.get("set_zero_by_class") or [])]
    low_util = [str(c) for c in (scoring.get("low_util_land_classes") or [])]
    return {
        "nullify_pins": nullify,
        "zero_classes": zero_classes,
        "low_util_classes": low_util,
    }


def null_building_sf_pins(db_path: Path, pins: list[str]) -> int:
    """NULL building_sf on the given PINs. Returns rows actually updated."""
    if not pins:
        return 0
    conn = get_connection(db_path)
    try:
        placeholders = ",".join("?" * len(pins))
        cur = conn.execute(
            f"UPDATE parcels SET building_sf = NULL "
            f"WHERE pin IN ({placeholders}) AND building_sf IS NOT NULL",
            pins,
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def zero_building_sf_by_class(db_path: Path, classes: list[str]) -> int:
    """Set building_sf = 0 for ALL parcels whose property_class is in
    `classes`, regardless of current value. Overwrites both NULL and
    populated assessor values so re-fetches can't reintroduce phantom SF.
    Returns rows actually updated."""
    if not classes:
        return 0
    conn = get_connection(db_path)
    try:
        placeholders = ",".join("?" * len(classes))
        cur = conn.execute(
            f"UPDATE parcels SET building_sf = 0 "
            f"WHERE property_class IN ({placeholders}) "
            f"AND (building_sf IS NULL OR building_sf <> 0)",
            classes,
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def derive_is_low_util_land(db_path: Path, classes: list[str]) -> int:
    """Set is_low_util_land = 1 for parcels whose property_class is in
    `classes`, 0 otherwise. Returns the count of low-util rows."""
    conn = get_connection(db_path)
    try:
        if classes:
            placeholders = ",".join("?" * len(classes))
            conn.execute(
                f"UPDATE parcels SET is_low_util_land = "
                f"  CASE WHEN property_class IN ({placeholders}) THEN 1 ELSE 0 END",
                classes,
            )
        else:
            conn.execute("UPDATE parcels SET is_low_util_land = 0")
        conn.commit()
        cur = conn.execute(
            "SELECT COUNT(*) AS n FROM parcels WHERE is_low_util_land = 1"
        ).fetchone()
        return cur["n"]
    finally:
        conn.close()


def cleanup(db_path: Path, corrections_path: Path = _CORRECTIONS_PATH) -> dict:
    """Run all cleanup steps; return per-step counts for logging."""
    corrections = load_corrections(corrections_path)
    return {
        "nulled_bad_building_sf": null_building_sf_pins(
            db_path, corrections["nullify_pins"]
        ),
        "zeroed_building_sf_by_class": zero_building_sf_by_class(
            db_path, corrections["zero_classes"]
        ),
        "low_util_land_count": derive_is_low_util_land(
            db_path, corrections["low_util_classes"]
        ),
    }


def _cli(argv: list[str] | None = None) -> int:
    import argparse
    p = argparse.ArgumentParser(prog="pipeline.cleanup",
                                description="Apply manual data corrections from "
                                            "config/data_corrections.yaml.")
    p.add_argument("--db", required=True, type=Path)
    p.add_argument("--corrections", default=_CORRECTIONS_PATH, type=Path,
                   help="Path to data_corrections.yaml (default: config/)")
    args = p.parse_args(argv)
    counts = cleanup(args.db, args.corrections)
    for k, v in counts.items():
        print(f"{k}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
