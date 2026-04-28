"""Historical-analysis script that derives initial scoring weights.

Looks at parcels that *did* get NEW CONSTRUCTION or WRECKING/DEMOLITION permits
(positives) vs. parcels that didn't (negatives) inside the target geography,
fits a logistic regression on z-scored continuous + raw binary features, and
emits config/scoring.yaml + a markdown analysis report.
"""
from __future__ import annotations
from pathlib import Path

from pipeline.config import GeographyConfig


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
