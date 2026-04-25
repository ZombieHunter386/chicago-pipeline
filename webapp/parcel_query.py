from __future__ import annotations
from typing import Any

# Whitelist of columns filters may reference. Prevents SQL injection via
# arbitrary filter keys — we interpolate column names into SQL directly.
ALLOWED_FILTER_COLUMNS = {
    "score", "is_absentee", "is_llc", "owner_name",
    "property_class", "lot_size_sf", "year_built", "condition",
    "zone_class", "allows_multifamily_by_right", "far_gap", "tif_district",
    "tax_delinquent", "open_violations_count", "has_vacancy_report",
    "years_since_last_permit", "hold_duration_years",
    "assessed_total", "land_building_ratio", "tax_increase_pct_5yr",
}

ALLOWED_STAGES = {"scored", "outreach", "responded", "introduced", "dead"}

DEFAULT_ORDER_BY = (
    "last_updated_date DESC, "
    "hold_duration_years IS NULL, hold_duration_years DESC"
)


def build_parcel_query(
    filters: dict[str, Any],
    stage: str | None,
    limit: int,
    offset: int,
) -> tuple[str, list]:
    """Return (sql, params) for the ranked list."""
    where_clauses, params = _build_where(filters, stage)
    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
    sql = (
        "SELECT pin, address, lat, lng, owner_name, property_class, lot_size_sf, "
        "year_built, zone_class, hold_duration_years, "
        "is_absentee, is_llc, tax_delinquent, open_violations_count, "
        "far_gap, stage, listing_status, score, consolidation_group_id "
        f"FROM parcels {where_sql} "
        f"ORDER BY {DEFAULT_ORDER_BY} "
        f"LIMIT {int(limit)} OFFSET {int(offset)}"
    )
    return sql, params


def build_count_query(
    filters: dict[str, Any],
    stage: str | None,
) -> tuple[str, list]:
    """Return (sql, params) for the total-count of matching rows."""
    where_clauses, params = _build_where(filters, stage)
    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
    sql = f"SELECT COUNT(*) AS n FROM parcels {where_sql}"
    return sql, params


def _build_where(filters: dict[str, Any], stage: str | None) -> tuple[list[str], list]:
    clauses: list[str] = []
    params: list = []

    for col, value in filters.items():
        if col not in ALLOWED_FILTER_COLUMNS:
            raise ValueError(f"unknown column: {col!r}")

        if isinstance(value, bool):
            # checkbox: only filter when checked (True -> col = 1)
            if value:
                clauses.append(f"{col} = 1")
        elif isinstance(value, dict):
            # range: {"min": x, "max": y}  -- either may be absent
            mn = value.get("min")
            mx = value.get("max")
            if mn is not None:
                clauses.append(f"{col} >= ?")
                params.append(mn)
            if mx is not None:
                clauses.append(f"{col} <= ?")
                params.append(mx)
        elif isinstance(value, (int, float)):
            clauses.append(f"{col} = ?")
            params.append(value)
        elif isinstance(value, str) and value != "":
            # text_search on owner_name / address — case-insensitive LIKE
            # dropdown selections land here too — exact match
            if col in {"owner_name", "address"}:
                clauses.append(f"UPPER({col}) LIKE UPPER(?)")
                params.append(f"%{value}%")
            else:
                clauses.append(f"{col} = ?")
                params.append(value)
        # else: empty string / None -> ignore (unfiltered)

    if stage is not None:
        if stage not in ALLOWED_STAGES:
            raise ValueError(f"unknown stage: {stage!r}")
        clauses.append("stage = ?")
        params.append(stage)

    return clauses, params
