from __future__ import annotations
from typing import Any

# Whitelist of columns filters may reference. Prevents SQL injection via
# arbitrary filter keys — we interpolate column names into SQL directly.
ALLOWED_FILTER_COLUMNS = {
    "score", "is_absentee", "is_llc", "owner_name", "address",
    "property_class", "lot_size_sf", "building_sf", "year_built", "condition",
    "zone_class", "allows_multifamily_by_right", "far_gap", "tif_district",
    "max_far", "min_lot_area_per_unit", "max_units_allowed",
    "tax_delinquent", "open_violations_count", "oldest_violation_age_days",
    "has_vacancy_report", "years_since_last_permit", "hold_duration_years",
    "appeal_count",
    "assessed_total", "estimated_annual_tax", "land_building_ratio",
    "tax_increase_pct_5yr", "last_sale_price", "last_sale_date",
    "ward_num", "cta_distance_ft",
    "is_condo_building", "condo_unit_count",
}

ALLOWED_STAGES = {"scored", "outreach", "responded", "introduced", "dead"}

ALLOWED_SORT_COLUMNS = {
    "first_seen_date", "last_updated_date", "score",
    "lot_size_sf", "year_built", "hold_duration_years",
    "assessed_total", "estimated_annual_tax",
    "tax_increase_pct_5yr", "land_building_ratio",
    "open_violations_count", "years_since_last_permit",
    "appeal_count", "oldest_violation_age_days",
    "condo_unit_count", "far_gap",
    "max_far", "min_lot_area_per_unit", "max_units_allowed",
    "last_sale_price", "last_sale_date",
    "building_sf", "cta_distance_ft",
    "address", "owner_name",
}

DEFAULT_ORDER_BY = (
    "last_updated_date DESC, "
    "hold_duration_years IS NULL, hold_duration_years DESC"
)


def build_parcel_query(
    filters: dict[str, Any],
    stage: str | None,
    limit: int,
    offset: int,
    include_condo_units: bool = False,
    sort: str | None = None,
    direction: str = "desc",
) -> tuple[str, list]:
    """Return (sql, params) for the ranked list."""
    where_clauses, params = _build_where(filters, stage, include_condo_units)
    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

    if sort is None:
        order_by = DEFAULT_ORDER_BY
    else:
        if sort not in ALLOWED_SORT_COLUMNS:
            raise ValueError(f"unknown sort column: {sort!r}")
        if direction.lower() not in {"asc", "desc"}:
            raise ValueError(f"unknown direction: {direction!r}")
        # NULL last in both directions so empty cells sink to the bottom.
        order_by = f"{sort} IS NULL, {sort} {direction.upper()}"

    sql = (
        "SELECT pin, address, lat, lng, owner_name, property_class, lot_size_sf, "
        "year_built, zone_class, hold_duration_years, "
        "is_absentee, is_llc, tax_delinquent, open_violations_count, "
        "far_gap, stage, listing_status, score, consolidation_group_id, "
        "is_condo_building, condo_unit_count "
        f"FROM parcels {where_sql} "
        f"ORDER BY {order_by} "
        f"LIMIT {int(limit)} OFFSET {int(offset)}"
    )
    return sql, params


def build_count_query(
    filters: dict[str, Any],
    stage: str | None,
    include_condo_units: bool = False,
) -> tuple[str, list]:
    """Return (sql, params) for the total-count of matching rows."""
    where_clauses, params = _build_where(filters, stage, include_condo_units)
    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
    sql = f"SELECT COUNT(*) AS n FROM parcels {where_sql}"
    return sql, params


def _build_where(
    filters: dict[str, Any],
    stage: str | None,
    include_condo_units: bool = False,
) -> tuple[list[str], list]:
    clauses: list[str] = []
    params: list = []

    if not include_condo_units:
        clauses.append("is_condo_unit = 0")

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
