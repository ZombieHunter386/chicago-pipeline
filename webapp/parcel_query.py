from __future__ import annotations
from typing import Any

# Whitelist of columns filters may reference. Prevents SQL injection via
# arbitrary filter keys — we interpolate column names into SQL directly.
ALLOWED_FILTER_COLUMNS = {
    "score", "is_absentee", "is_llc", "owner_name", "address",
    "property_class", "lot_size_sf", "building_sf", "year_built", "condition",
    "zone_class", "allows_multifamily_by_right", "far_gap", "far_gap_delta",
    "tif_district",
    "max_far", "min_lot_area_per_unit", "max_units_allowed",
    "tax_delinquent", "open_violations_count", "oldest_violation_age_days",
    "has_vacancy_report", "years_since_last_permit", "hold_duration_years",
    "appeal_count",
    "assessed_total", "estimated_annual_tax", "land_building_ratio",
    "tax_increase_pct_5yr", "last_sale_price", "last_sale_date",
    "ward_num", "cta_distance_ft",
    "is_condo_building", "condo_unit_count",
    # Added 2026-04-27 with footprints + scofflaw + vacant-violation sources
    "unit_count", "is_scofflaw", "scofflaw_appearances_count",
    "vacant_violations_count", "vacant_violations_amount_due",
    "building_sf_source", "condition_source",
}

ALLOWED_STAGES = {"scored", "outreach", "responded", "introduced", "dead"}

ALLOWED_SORT_COLUMNS = {
    "first_seen_date", "last_updated_date", "score",
    "lot_size_sf", "year_built", "hold_duration_years",
    "assessed_total", "estimated_annual_tax",
    "tax_increase_pct_5yr", "land_building_ratio",
    "open_violations_count", "years_since_last_permit",
    "appeal_count", "oldest_violation_age_days",
    "condo_unit_count", "far_gap", "far_gap_delta",
    "max_far", "min_lot_area_per_unit", "max_units_allowed",
    "last_sale_price", "last_sale_date",
    "building_sf", "cta_distance_ft",
    "address", "owner_name",
    "unit_count", "scofflaw_appearances_count",
    "vacant_violations_count", "vacant_violations_amount_due",
}

DEFAULT_ORDER_BY = (
    "last_updated_date DESC, "
    "hold_duration_years IS NULL, hold_duration_years DESC"
)


PARCEL_LIST_COLUMNS = (
    "*"  # placeholder — see SELECT below
)


# Categories that the layer toggles surface — must stay aligned with
# webapp/routes.py:_map_category() so the SQL filter and the assigned
# server-side category label agree.
ALLOWED_CATEGORIES = {"top", "consolidated", "outreach", "other"}


def _category_clause(visible_categories: set[str], top_n_threshold: float | None) -> str | None:
    """Build a SQL clause that keeps rows whose category is in
    visible_categories. Returns None when all categories are visible
    (no-op filter). Mirrors the bucketing rule from _map_category()."""
    if not visible_categories or visible_categories == ALLOWED_CATEGORIES:
        return None

    parts = []
    if "outreach" in visible_categories:
        parts.append("stage = 'outreach'")
    if "consolidated" in visible_categories:
        parts.append("(stage IS NULL OR stage <> 'outreach') "
                     "AND (consolidation_group_id IS NOT NULL "
                     "OR is_condo_building = 1)")
    if "top" in visible_categories and top_n_threshold is not None:
        parts.append("(stage IS NULL OR stage <> 'outreach') "
                     "AND consolidation_group_id IS NULL "
                     "AND COALESCE(is_condo_building, 0) = 0 "
                     f"AND score >= {top_n_threshold}")
    if "other" in visible_categories:
        # "other" = none of the above buckets.
        if top_n_threshold is not None:
            parts.append("(stage IS NULL OR stage <> 'outreach') "
                         "AND consolidation_group_id IS NULL "
                         "AND COALESCE(is_condo_building, 0) = 0 "
                         f"AND (score IS NULL OR score < {top_n_threshold})")
        else:
            parts.append("(stage IS NULL OR stage <> 'outreach') "
                         "AND consolidation_group_id IS NULL "
                         "AND COALESCE(is_condo_building, 0) = 0")
    if not parts:
        # Caller passed only category names that need a threshold we don't
        # have — fall through to "match nothing" rather than silently match
        # everything.
        return "1 = 0"
    return "(" + " OR ".join(f"({p})" for p in parts) + ")"


def build_parcel_query(
    filters: dict[str, Any],
    stage: str | None,
    limit: int,
    offset: int,
    include_condo_units: bool = False,
    sort: str | None = None,
    direction: str = "desc",
    top_n_only: bool = False,
    top_n_threshold: float | None = None,
    visible_categories: set[str] | None = None,
) -> tuple[str, list]:
    """Return (sql, params) for the ranked list."""
    where_clauses, params = _build_where(filters, stage, include_condo_units)
    if top_n_only and top_n_threshold is not None:
        where_clauses.append("score >= ?")
        params.append(top_n_threshold)
    if visible_categories:
        cat_clause = _category_clause(visible_categories, top_n_threshold)
        if cat_clause:
            where_clauses.append(cat_clause)
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
        "far_gap, far_gap_delta, max_far, min_lot_area_per_unit, max_units_allowed, "
        "stage, listing_status, score, consolidation_group_id, "
        "is_condo_building, condo_unit_count, condo_units_missing_sf_count, "
        "building_sf, cta_distance_ft, "
        "assessed_total, estimated_annual_tax, tax_increase_pct_5yr, "
        "land_building_ratio, last_sale_price, last_sale_date, "
        "years_since_last_permit, appeal_count, oldest_violation_age_days, "
        "first_seen_date, last_updated_date, "
        "scofflaw_appearances_count, vacant_violations_amount_due, "
        "unit_count, is_scofflaw, vacant_violations_count "
        f"FROM parcels {where_sql} "
        f"ORDER BY {order_by} "
        f"LIMIT {int(limit)} OFFSET {int(offset)}"
    )
    return sql, params


def build_count_query(
    filters: dict[str, Any],
    stage: str | None,
    include_condo_units: bool = False,
    top_n_only: bool = False,
    top_n_threshold: float | None = None,
    visible_categories: set[str] | None = None,
) -> tuple[str, list]:
    """Return (sql, params) for the total-count of matching rows."""
    where_clauses, params = _build_where(filters, stage, include_condo_units)
    if top_n_only and top_n_threshold is not None:
        where_clauses.append("score >= ?")
        params.append(top_n_threshold)
    if visible_categories:
        cat_clause = _category_clause(visible_categories, top_n_threshold)
        if cat_clause:
            where_clauses.append(cat_clause)
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
            # tri-state: True -> col = 1, False -> col = 0, absent -> no filter
            clauses.append(f"{col} = {1 if value else 0}")
        elif isinstance(value, (list, tuple)):
            # multi-select: col IN (?, ?, ?). Empty list -> match nothing
            # so an explicit "no values selected" doesn't silently match all.
            if not value:
                clauses.append("1 = 0")
            else:
                placeholders = ",".join("?" * len(value))
                clauses.append(f"{col} IN ({placeholders})")
                params.extend(value)
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
