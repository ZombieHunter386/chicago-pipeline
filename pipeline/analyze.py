"""Historical-analysis script that derives initial scoring weights.

Looks at parcels that *did* get NEW CONSTRUCTION or WRECKING/DEMOLITION permits
(positives) vs. parcels that didn't (negatives) inside the target geography,
fits a logistic regression on z-scored continuous + raw binary features, and
emits config/scoring.yaml + a markdown analysis report.
"""
from __future__ import annotations
import sqlite3
from pathlib import Path

from pipeline.config import GeographyConfig
from pipeline.db import get_connection
from pipeline.spatial import (
    DEFAULT_GEO_RADIUS_FT,
    match_records_to_parcels_with_address,
)


# Signals consumed by the v1 model. Each entry is (column_name, kind, source_table).
# Excluded on purpose: tax_delinquent (0% pop), has_vacancy_report (defunct dataset),
# building_sf / year_built / condition / built_far (~22% pop, condo + commercial gap).
SIGNALS: list[tuple[str, str, str]] = [
    # Continuous
    ("lot_size_sf",            "continuous", "parcels"),
    ("hold_duration_years",    "continuous", "parcels"),
    ("max_far",                "continuous", "parcels"),
    ("far_gap_delta",          "continuous", "parcels"),
    ("land_building_ratio",    "continuous", "parcels"),
    ("estimated_annual_tax",   "continuous", "parcels"),
    ("tax_increase_pct_5yr",   "continuous", "parcels"),
    ("cta_distance_ft",        "continuous", "parcels"),
    ("appeal_count",           "continuous", "parcels"),
    ("open_violations_count",  "continuous", "parcels"),
    ("years_since_last_permit","continuous", "parcels"),
    ("vacant_violations_count","continuous", "parcels"),
    ("scofflaw_appearances_count", "continuous", "parcels"),
    # Binary (0/1 in the parcels table)
    ("is_absentee",                "binary",     "parcels"),
    ("is_llc",                     "binary",     "parcels"),
    ("allows_multifamily_by_right","binary",     "parcels"),
    ("is_scofflaw",                "binary",     "parcels"),
]


# Permit types that count as a development event. Match by prefix because the
# raw permit_type strings vary slightly ("PERMIT - NEW CONSTRUCTION", sometimes
# trailing whitespace or sub-type qualifiers).
QUALIFYING_PERMIT_PREFIXES = (
    "PERMIT - NEW CONSTRUCTION",
    "PERMIT - WRECKING/DEMOLITION",
)


def _is_qualifying_permit(permit_type: str | None) -> bool:
    if not permit_type:
        return False
    pt = permit_type.strip().upper()
    return any(pt.startswith(p) for p in QUALIFYING_PERMIT_PREFIXES)


def _permit_record_address(r: dict) -> str | None:
    """Same address builder used by sources/cdp_permits.py — kept duplicated
    here to keep analyze decoupled from the fetch source modules."""
    parts = [
        (r.get("street_number") or "").strip(),
        (r.get("street_direction") or "").strip(),
        (r.get("street_name") or "").strip(),
    ]
    parts = [p for p in parts if p]
    return " ".join(parts) if parts else None


def identify_positive_examples(db_path: Path) -> dict[str, int]:
    """Find PINs with at least one NEW CONSTRUCTION or WRECKING/DEMOLITION
    permit in raw_cdp_permits. Returns {pin: earliest_qualifying_year}.

    "Earliest" because the *event year* is the redevelopment trigger; if a
    PIN had both a demo and a follow-up new-build, the demo's year is the
    boundary the pre-development snapshot should sit before.
    """
    conn = get_connection(db_path)
    try:
        permit_rows = [dict(r) for r in conn.execute(
            "SELECT permit_number, permit_type, issue_date, "
            "       street_number, street_direction, street_name, latitude, longitude "
            "FROM raw_cdp_permits"
        ).fetchall()]
        parcels = [dict(r) for r in conn.execute(
            "SELECT pin, address, lat, lng FROM parcels"
        ).fetchall()]
    finally:
        conn.close()

    qualifying = [r for r in permit_rows if _is_qualifying_permit(r["permit_type"])]
    if not qualifying or not parcels:
        return {}

    matches, _fuzzy = match_records_to_parcels_with_address(
        qualifying, parcels,
        get_record_address=_permit_record_address,
        geo_radius_ft=DEFAULT_GEO_RADIUS_FT,
    )

    earliest: dict[str, int] = {}
    for idx, (pin, _method) in matches.items():
        date_str = qualifying[idx]["issue_date"]
        if not date_str:
            continue
        year = int(date_str[:4])
        if pin not in earliest or year < earliest[pin]:
            earliest[pin] = year
    return earliest


def analyze(
    db_path: Path,
    geo: GeographyConfig,
    scoring_yaml_path: Path,
    report_md_path: Path,
) -> None:
    """Entry point — orchestrates positive identification, training-set
    construction, regression fitting, weight derivation, and writing the
    two output files. Filled in across Tasks 3-10."""
    raise NotImplementedError("Implemented in Task 10")
