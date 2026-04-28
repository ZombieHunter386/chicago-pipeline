"""Aggregate a consolidation group's constituent-parcel signals into a single
feature dict that mirrors the parcels-row shape Analyze (training) and
Score (scoring) consume.

Per-signal rules locked by the plan:
  - lot_size_sf, building_sf:            from consolidation_groups (combined_*)
  - hold_duration_years, cta_distance_ft,
    years_since_last_permit:             MIN across constituents
  - estimated_annual_tax, appeal_count,
    open_violations_count, vacant_violations_count,
    scofflaw_appearances_count:          SUM
  - tax_increase_pct_5yr:                AVG weighted by assessed_total
  - is_absentee, is_llc, is_scofflaw,
    allows_multifamily_by_right:         MAX (any → 1)
  - max_far:                             MAX
  - far_gap_delta:                       MAX(max_far) - combined_building_sf/combined_lot_size_sf
  - land_building_ratio:                 SUM(assessed_land)/SUM(assessed_total)

Signals where every constituent is NULL → None (Score's normalize_signal
treats None as 0.5 for continuous and 0 for binary)."""
from __future__ import annotations
import json
from pathlib import Path

from pipeline.db import get_connection


# Columns we need to read from each constituent parcel.
_QUERY_COLUMNS = (
    "lot_size_sf", "building_sf", "hold_duration_years",
    "estimated_annual_tax", "tax_increase_pct_5yr",
    "cta_distance_ft", "appeal_count", "open_violations_count",
    "years_since_last_permit", "vacant_violations_count",
    "scofflaw_appearances_count",
    "is_absentee", "is_llc", "is_scofflaw",
    "allows_multifamily_by_right", "max_far",
    "assessed_land", "assessed_total",
)


def _min_nonnull(rows, col):
    vals = [r[col] for r in rows if r[col] is not None]
    return min(vals) if vals else None


def _max_nonnull(rows, col):
    vals = [r[col] for r in rows if r[col] is not None]
    return max(vals) if vals else None


def _sum_nonnull(rows, col):
    vals = [r[col] for r in rows if r[col] is not None]
    return sum(vals) if vals else None


def _binary_max(rows, col):
    """For binary 0/1 columns: return 1 if any constituent is 1, else 0.
    Treat NULL as 0 (not flagged). Returns int."""
    return int(any(r[col] for r in rows if r[col] is not None))


def _weighted_avg(rows, value_col, weight_col):
    """Weighted average of value_col by weight_col. Both must be non-null
    on the same row to be counted. If total weight is 0, return None."""
    total_weight = 0.0
    weighted_sum = 0.0
    for r in rows:
        v, w = r[value_col], r[weight_col]
        if v is None or w is None:
            continue
        total_weight += w
        weighted_sum += w * v
    return weighted_sum / total_weight if total_weight > 0 else None


def derive_group_features(group_id: int, db_path: Path) -> dict:
    """Aggregate a consolidation group's constituent signals into a feature
    dict matching the parcels-row shape that Score's score_parcel and
    Analyze's build_training_table consume."""
    conn = get_connection(db_path)
    try:
        group = conn.execute(
            "SELECT pins, combined_lot_size_sf, combined_building_sf "
            "FROM consolidation_groups WHERE group_id = ?",
            (group_id,),
        ).fetchone()
        if group is None:
            raise ValueError(f"consolidation group {group_id} not found")
        pins = json.loads(group["pins"])
        if not pins:
            raise ValueError(f"consolidation group {group_id} has no pins")
        placeholders = ", ".join("?" * len(pins))
        cols = ", ".join(_QUERY_COLUMNS)
        constituents = [dict(r) for r in conn.execute(
            f"SELECT {cols} FROM parcels WHERE pin IN ({placeholders})",
            pins,
        ).fetchall()]
    finally:
        conn.close()

    if not constituents:
        # No constituents found in parcels (data integrity issue) — return
        # all-None signal dict; caller decides whether to skip.
        return {col: None for col in _QUERY_COLUMNS}

    combined_lot = group["combined_lot_size_sf"]
    combined_bldg = group["combined_building_sf"]

    # MAX max_far for far_gap_delta recomputation
    max_far_val = _max_nonnull(constituents, "max_far")

    # Recomputed far_gap_delta — needs all three components
    if max_far_val is not None and combined_bldg is not None and combined_lot:
        far_gap = max_far_val - (combined_bldg / combined_lot)
    else:
        far_gap = None

    # Recomputed land_building_ratio
    sum_land = _sum_nonnull(constituents, "assessed_land")
    sum_total = _sum_nonnull(constituents, "assessed_total")
    if sum_land is not None and sum_total and sum_total > 0:
        land_ratio = sum_land / sum_total
    else:
        land_ratio = None

    return {
        "lot_size_sf": combined_lot,
        "building_sf": combined_bldg,
        "hold_duration_years":     _min_nonnull(constituents, "hold_duration_years"),
        "estimated_annual_tax":    _sum_nonnull(constituents, "estimated_annual_tax"),
        "tax_increase_pct_5yr":    _weighted_avg(constituents, "tax_increase_pct_5yr",
                                                 "assessed_total"),
        "cta_distance_ft":         _min_nonnull(constituents, "cta_distance_ft"),
        "appeal_count":            _sum_nonnull(constituents, "appeal_count"),
        "open_violations_count":   _sum_nonnull(constituents, "open_violations_count"),
        "years_since_last_permit": _min_nonnull(constituents, "years_since_last_permit"),
        "vacant_violations_count": _sum_nonnull(constituents, "vacant_violations_count"),
        "scofflaw_appearances_count": _sum_nonnull(constituents, "scofflaw_appearances_count"),
        "is_absentee":             _binary_max(constituents, "is_absentee"),
        "is_llc":                  _binary_max(constituents, "is_llc"),
        "is_scofflaw":             _binary_max(constituents, "is_scofflaw"),
        "allows_multifamily_by_right": _binary_max(constituents, "allows_multifamily_by_right"),
        "max_far":                 max_far_val,
        "far_gap_delta":           far_gap,
        "land_building_ratio":     land_ratio,
    }
