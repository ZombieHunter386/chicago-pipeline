# pipeline/db.py
from __future__ import annotations
import sqlite3
from pathlib import Path
from typing import Iterable


SCHEMA_SQL = """
-- ============================================================
-- Main parcels table — derived/aggregated fields per PIN.
-- Raw source data lives in raw_* tables.
-- ============================================================
CREATE TABLE IF NOT EXISTS parcels (
    pin TEXT PRIMARY KEY,
    pin10 TEXT,
    address TEXT,
    lat REAL,
    lng REAL,
    ward_num TEXT,
    zip_code TEXT,
    -- Owner
    owner_name TEXT,
    owner_address TEXT,
    mail_name TEXT,
    mail_address TEXT,
    is_absentee INTEGER,        -- 0/1
    is_llc INTEGER,             -- 0/1
    -- Building
    property_class TEXT,
    lot_size_sf REAL,
    building_sf REAL,
    year_built INTEGER,
    condition TEXT,
    building_classification TEXT,
    zone_class TEXT,
    -- Values / taxes
    assessed_land REAL,
    assessed_building REAL,
    assessed_total REAL,
    land_building_ratio REAL,
    estimated_annual_tax REAL,
    tax_increase_pct_1yr REAL,
    tax_increase_pct_5yr REAL,
    -- Sales
    last_sale_date TEXT,
    last_sale_price REAL,
    hold_duration_years REAL,
    deed_type TEXT,
    -- Signals
    tax_delinquent INTEGER,
    delinquency_years INTEGER,
    open_violations_count INTEGER,
    oldest_violation_age_days INTEGER,
    appeal_count INTEGER,
    has_vacancy_report INTEGER,
    years_since_last_permit REAL,
    -- Zoning
    max_far REAL,
    built_far REAL,
    far_gap REAL,
    far_gap_delta REAL,             -- max_far - built_far ("FAR points available")
    allows_multifamily_by_right INTEGER,
    min_lot_area_per_unit REAL,
    max_units_allowed INTEGER,
    tif_district TEXT,
    cta_nearest_station TEXT,
    cta_distance_ft REAL,
    -- Scoring (populated in Plan 2)
    score REAL,
    score_version TEXT,
    consolidation_group_id INTEGER,
    is_condo_unit INTEGER DEFAULT 0,
    is_condo_building INTEGER DEFAULT 0,
    condo_unit_count INTEGER,
    -- Building (added 2026-04-27 with footprints + scofflaw + vacant-violation sources)
    unit_count INTEGER,
    building_sf_source TEXT,        -- 'assessor' | 'footprint'
    condition_source TEXT,          -- 'assessor' | 'footprint'
    is_scofflaw INTEGER,
    scofflaw_appearances_count INTEGER,
    most_recent_scofflaw_list_date TEXT,
    vacant_violations_count INTEGER,
    vacant_violations_amount_due REAL,
    most_recent_vacant_violation_date TEXT,
    -- Listing (populated in Plan 4)
    listing_status TEXT,
    listing_check_date TEXT,
    -- Status
    stage TEXT DEFAULT 'scored',
    first_seen_date TEXT,
    last_updated_date TEXT,
    last_fetched_date TEXT
);
CREATE INDEX IF NOT EXISTS idx_parcels_zone_class ON parcels(zone_class);
CREATE INDEX IF NOT EXISTS idx_parcels_property_class ON parcels(property_class);
CREATE INDEX IF NOT EXISTS idx_parcels_score ON parcels(score);
CREATE INDEX IF NOT EXISTS idx_parcels_stage ON parcels(stage);
CREATE INDEX IF NOT EXISTS idx_parcels_last_updated_date ON parcels(last_updated_date);
CREATE INDEX IF NOT EXISTS idx_parcels_hold_duration_years ON parcels(hold_duration_years);
CREATE INDEX IF NOT EXISTS idx_parcels_is_absentee ON parcels(is_absentee);
CREATE INDEX IF NOT EXISTS idx_parcels_is_llc ON parcels(is_llc);
CREATE INDEX IF NOT EXISTS idx_parcels_tax_delinquent ON parcels(tax_delinquent);
CREATE INDEX IF NOT EXISTS idx_parcels_consolidation_group_id ON parcels(consolidation_group_id);
CREATE INDEX IF NOT EXISTS idx_parcels_pin10 ON parcels(pin10);
CREATE INDEX IF NOT EXISTS idx_parcels_is_condo_unit ON parcels(is_condo_unit);
CREATE INDEX IF NOT EXISTS idx_parcels_is_condo_building ON parcels(is_condo_building);
CREATE INDEX IF NOT EXISTS idx_parcels_max_units_allowed ON parcels(max_units_allowed);

-- ============================================================
-- Consolidation groups — adjacent same-owner parcels
-- ============================================================
CREATE TABLE IF NOT EXISTS consolidation_groups (
    group_id INTEGER PRIMARY KEY AUTOINCREMENT,
    pins TEXT NOT NULL,                  -- JSON array of PINs
    combined_lot_size_sf REAL,
    combined_building_sf REAL,
    owner_name TEXT,
    detected_date TEXT,
    score REAL,
    score_version TEXT
);

-- ============================================================
-- Contacts (populated in Plan 4)
-- ============================================================
CREATE TABLE IF NOT EXISTS contacts (
    contact_id INTEGER PRIMARY KEY AUTOINCREMENT,
    pin TEXT,
    consolidation_group_id INTEGER,
    name TEXT,
    phone TEXT,
    email TEXT,
    mailing_address TEXT,
    role TEXT,
    source TEXT,
    FOREIGN KEY(pin) REFERENCES parcels(pin),
    FOREIGN KEY(consolidation_group_id) REFERENCES consolidation_groups(group_id)
);

-- ============================================================
-- Waves (populated in Plan 4)
-- ============================================================
CREATE TABLE IF NOT EXISTS waves (
    wave_id INTEGER PRIMARY KEY AUTOINCREMENT,
    start_date TEXT,
    end_date TEXT,
    parcels_contacted INTEGER DEFAULT 0,
    responses_received INTEGER DEFAULT 0,
    leads_introduced INTEGER DEFAULT 0,
    notes TEXT,
    config_version TEXT
);

-- ============================================================
-- Outreach (populated in Plan 4)
-- ============================================================
CREATE TABLE IF NOT EXISTS outreach (
    outreach_id INTEGER PRIMARY KEY AUTOINCREMENT,
    wave_id INTEGER,
    pin TEXT,
    consolidation_group_id INTEGER,
    contact_id INTEGER,
    channel TEXT,
    touch_number INTEGER,
    sent_date TEXT,
    response_date TEXT,
    response_type TEXT,
    handed_off INTEGER DEFAULT 0,
    handed_off_date TEXT,
    draft_subject TEXT,
    draft_body TEXT,
    final_body TEXT,
    lob_tracking_id TEXT,
    lob_status TEXT,
    notes TEXT,
    FOREIGN KEY(wave_id) REFERENCES waves(wave_id),
    FOREIGN KEY(pin) REFERENCES parcels(pin),
    FOREIGN KEY(consolidation_group_id) REFERENCES consolidation_groups(group_id),
    FOREIGN KEY(contact_id) REFERENCES contacts(contact_id)
);

-- ============================================================
-- Raw source tables — store everything verbatim from APIs.
-- One table per data source. Field names mirror source schema.
-- ============================================================

-- Source 1A: Cook County Assessor — Parcel Universe
CREATE TABLE IF NOT EXISTS raw_assessor_parcels (
    pin TEXT,
    year TEXT,
    pin10 TEXT,
    class TEXT,
    lat REAL,
    lon REAL,
    ward_num TEXT,
    zip_code TEXT,
    tax_tif_district_num TEXT,
    tax_tif_district_name TEXT,
    township_code TEXT,
    nbhd_code TEXT,
    fetched_at TEXT,
    PRIMARY KEY(pin, year)
);

-- Source 1B: Cook County Assessor — Parcel Addresses
CREATE TABLE IF NOT EXISTS raw_assessor_addresses (
    pin TEXT PRIMARY KEY,
    prop_address_full TEXT,
    prop_address_city_name TEXT,
    prop_address_state TEXT,
    prop_address_zipcode_1 TEXT,
    mail_address_name TEXT,
    mail_address_full TEXT,
    mail_address_city_name TEXT,
    mail_address_state TEXT,
    mail_address_zipcode_1 TEXT,
    owner_address_name TEXT,
    owner_address_full TEXT,
    fetched_at TEXT
);

-- Source 1C: Improvement Characteristics
CREATE TABLE IF NOT EXISTS raw_assessor_characteristics (
    pin TEXT,
    year TEXT,
    class TEXT,
    char_land_sf REAL,
    char_bldg_sf REAL,
    char_yrblt TEXT,
    char_cnst_qlty TEXT,
    char_repair_cnd TEXT,
    cdu TEXT,
    char_beds TEXT,
    char_rooms TEXT,
    char_fbath TEXT,
    char_hbath TEXT,
    char_type_resd TEXT,
    char_ext_wall TEXT,
    char_heat TEXT,
    char_bsmt TEXT,
    char_bsmt_fin TEXT,
    char_gar1_att TEXT,
    char_gar1_area TEXT,
    char_use TEXT,
    char_site TEXT,
    char_air TEXT,
    pin_is_multicard INTEGER,
    pin_num_cards INTEGER,
    fetched_at TEXT,
    PRIMARY KEY(pin, year)
);

-- Source 1D: Assessed Values
CREATE TABLE IF NOT EXISTS raw_assessor_values (
    pin TEXT,
    year TEXT,
    mailed_bldg REAL,
    mailed_land REAL,
    mailed_tot REAL,
    certified_bldg REAL,
    certified_land REAL,
    certified_tot REAL,
    board_bldg REAL,
    board_land REAL,
    board_tot REAL,
    board_hie REAL,
    fetched_at TEXT,
    PRIMARY KEY(pin, year)
);

-- Source 1E: Parcel Sales
CREATE TABLE IF NOT EXISTS raw_assessor_sales (
    pin TEXT,
    sale_date TEXT,
    sale_price REAL,
    seller_name TEXT,
    buyer_name TEXT,
    deed_type TEXT,
    doc_no TEXT,
    is_multisale INTEGER,
    num_parcels_sale INTEGER,
    sale_filter_same_sale_within_365 INTEGER,
    sale_filter_less_than_10k INTEGER,
    sale_filter_deed_type INTEGER,
    fetched_at TEXT,
    PRIMARY KEY(pin, sale_date, doc_no)
);

-- Source 1F: Appeals
CREATE TABLE IF NOT EXISTS raw_assessor_appeals (
    pin TEXT,
    year TEXT,
    appeal_outcome TEXT,
    assessed_value_change REAL,
    fetched_at TEXT,
    PRIMARY KEY(pin, year)
);

-- Source 1G: Tax-Exempt Parcels
CREATE TABLE IF NOT EXISTS raw_assessor_exempt (
    pin TEXT PRIMARY KEY,
    exemption_type TEXT,
    fetched_at TEXT
);

-- Source 2A: Zoning Districts
CREATE TABLE IF NOT EXISTS raw_cdp_zoning (
    objectid TEXT PRIMARY KEY,
    zone_class TEXT,
    geom_geojson TEXT,
    pd_num TEXT,
    fetched_at TEXT
);

-- Source 2C: Building Permits
CREATE TABLE IF NOT EXISTS raw_cdp_permits (
    permit_number TEXT PRIMARY KEY,
    permit_type TEXT,
    issue_date TEXT,
    street_number TEXT,
    street_direction TEXT,
    street_name TEXT,
    work_description TEXT,
    reported_cost REAL,
    community_area TEXT,
    ward TEXT,
    latitude REAL,
    longitude REAL,
    fetched_at TEXT
);

-- Source 2D: Building Violations
CREATE TABLE IF NOT EXISTS raw_cdp_violations (
    violation_id TEXT PRIMARY KEY,
    violation_date TEXT,
    violation_code TEXT,
    violation_status TEXT,
    violation_description TEXT,
    inspection_category TEXT,
    department_bureau TEXT,
    address TEXT,
    street_number TEXT,
    street_direction TEXT,
    street_name TEXT,
    property_group TEXT,
    latitude REAL,
    longitude REAL,
    fetched_at TEXT
);

-- Source 2E: Vacant and Abandoned Buildings
CREATE TABLE IF NOT EXISTS raw_cdp_vacant (
    service_request_number TEXT PRIMARY KEY,
    date_service_request_was_received TEXT,
    location_of_building_on_the_lot TEXT,
    is_the_building_dangerous_or_hazardous TEXT,
    address_street_number TEXT,
    address_street_direction TEXT,
    address_street_name TEXT,
    latitude REAL,
    longitude REAL,
    fetched_at TEXT
);

-- Source 2F: CTA L Stations
CREATE TABLE IF NOT EXISTS raw_cdp_cta_stations (
    station_id TEXT PRIMARY KEY,
    longname TEXT,
    lines TEXT,
    latitude REAL,
    longitude REAL,
    fetched_at TEXT
);

-- Source: City of Chicago ADU Eligibility Map (ArcGIS Online).
-- Each row is one polygon from the City's "ADUAllowedRS2AA_view" layer.
-- Polygons demarcate RS-zoned areas where ADUs are allowed (with varying
-- restrictions per polygon). Used by the chicago_adu_zones source to
-- spatial-join parcel centroids and derive parcels.adu_eligible +
-- parcels.adu_restriction_text.
CREATE TABLE IF NOT EXISTS raw_chicago_adu_zones (
    zone_id TEXT PRIMARY KEY,
    adu_area_code TEXT,
    restriction_text TEXT,
    polygon_wkt TEXT,
    fetched_at TEXT
);

-- Source 1H: Cook County GIS Parcel Boundaries (polygon area + dimensions)
CREATE TABLE IF NOT EXISTS raw_ccgis_parcels (
    pin10 TEXT PRIMARY KEY,
    area_sf REAL,
    width_ft REAL,
    depth_ft REAL,
    fetched_at TEXT
);

-- Source 3A: Cook County Clerk — Delinquent Property Tax
CREATE TABLE IF NOT EXISTS raw_clerk_delinquent (
    pin TEXT PRIMARY KEY,
    delinquent_years INTEGER,
    earliest_delinquent_year INTEGER,
    total_owed REAL,
    fetched_at TEXT
);

-- Source 2G: Building Code Scofflaw List (crg5-4zyp)
CREATE TABLE IF NOT EXISTS raw_cdp_scofflaw (
    record_id TEXT PRIMARY KEY,
    address TEXT,
    secondary_address TEXT,
    tertiary_address TEXT,
    defendant_owner TEXT,
    circuit_court_case_number TEXT,
    building_list_date TEXT,
    owner_list_date TEXT,
    community_area TEXT,
    ward TEXT,
    latitude REAL,
    longitude REAL,
    fetched_at TEXT
);

-- Source 2H: Vacant and Abandoned Buildings — Violations (kc9i-wq85)
CREATE TABLE IF NOT EXISTS raw_cdp_vacant_violations (
    docket_number TEXT PRIMARY KEY,
    violation_number TEXT,
    issued_date TEXT,
    issuing_department TEXT,
    last_hearing_date TEXT,
    property_address TEXT,
    violation_type TEXT,
    entity_or_person TEXT,
    disposition_description TEXT,
    total_fines REAL,
    total_administrative_costs REAL,
    interest_amount REAL,
    collection_costs_or_attorney_fees REAL,
    court_cost REAL,
    original_total_amount_due REAL,
    total_paid REAL,
    current_amount_due REAL,
    latitude REAL,
    longitude REAL,
    fetched_at TEXT
);

-- Source 2I: Chicago Building Footprints (syp8-uezg) — frozen at 2010-2011
CREATE TABLE IF NOT EXISTS raw_cdp_building_footprints (
    bldg_id TEXT PRIMARY KEY,
    bldg_statu TEXT,
    f_add1 TEXT,
    t_add1 TEXT,
    pre_dir1 TEXT,
    st_name1 TEXT,
    st_type1 TEXT,
    suf_dir1 TEXT,
    bldg_sq_fo REAL,
    shape_area REAL,
    stories INTEGER,
    no_of_unit INTEGER,
    year_built INTEGER,
    bldg_condi TEXT,
    demolished TEXT,
    edit_date TEXT,
    geom_geojson TEXT,
    centroid_lat REAL,
    centroid_lng REAL,
    fetched_at TEXT
);

-- ============================================================
-- Fetch log — one row per source per fetch run
-- ============================================================
CREATE TABLE IF NOT EXISTS fetch_log (
    log_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_name TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    rows_fetched INTEGER,
    status TEXT,                 -- 'ok' | 'error'
    error_message TEXT
);
CREATE INDEX IF NOT EXISTS idx_fetch_log_source ON fetch_log(source_name, started_at);
"""


def get_connection(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn


# Columns added after the original schema. ALTER TABLE on each at init time so
# existing DBs gain the new columns without losing data. SQLite raises
# "duplicate column" if the column already exists; we swallow that.
_LATER_COLUMNS = {
    "parcels": (
        ("unit_count", "INTEGER"),
        ("building_sf_source", "TEXT"),
        ("condition_source", "TEXT"),
        ("is_scofflaw", "INTEGER"),
        ("scofflaw_appearances_count", "INTEGER"),
        ("most_recent_scofflaw_list_date", "TEXT"),
        ("vacant_violations_count", "INTEGER"),
        ("vacant_violations_amount_due", "REAL"),
        ("most_recent_vacant_violation_date", "TEXT"),
        ("far_gap_delta", "REAL"),
        # Derived from property_class — true for low-utilization land classes
        # (parking lots, etc.). Used in scoring as the "underutilized site"
        # signal that replaced is_llc.
        ("is_low_util_land", "INTEGER"),
        # Count of condo unit constituents whose source building_sf was NULL
        # at rollup time. >0 means the rep's summed building_sf understates
        # the building total — UI surfaces this as "(SF incomplete)".
        ("condo_units_missing_sf_count", "INTEGER"),
        # outreach_paused=1 stops the cadence engine from surfacing this parcel
        # in Due Today. Manually toggled via POST /api/parcels/<pin>/pause.
        ("outreach_paused", "INTEGER DEFAULT 0"),
        # Scoring profiles (2026-06-08 spec): ADU + Redevelopment.
        # ---- Phase 1 (schema). Populated in later phases:
        # Lot geometry from CCGIS polygons (populated in Phase 2).
        ("lot_width_ft", "REAL"),
        ("lot_depth_ft", "REAL"),
        # ADU eligibility from City ArcGIS layer + zone_class derivation (Phase 3).
        ("adu_eligible", "INTEGER"),
        ("adu_restriction_text", "TEXT"),
        ("adu_has_annual_limits", "INTEGER"),
        # Derived from last_sale_price + hold_duration_years (Phase 4, in pipeline/score.py).
        ("last_sale_price_recent", "REAL"),
        # Per-profile scores written by the scoring engine (Phase 4).
        ("score_adu", "REAL"),
        ("score_redev", "REAL"),
    ),
    # Gmail message id from successful sends; null for manual touches.
    # Replaces the prior "shove it into notes" hack — clean column, one
    # purpose, easy to query.
    "outreach": (
        ("gmail_message_id", "TEXT"),
    ),
    # combined_building_sf was added to consolidation_groups in a prior commit
    # but the migration was never written; CREATE TABLE IF NOT EXISTS doesn't
    # add columns to a pre-existing table.
    "consolidation_groups": (
        ("combined_building_sf", "REAL"),
        ("score", "REAL"),
        ("score_version", "TEXT"),
    ),
    # Lot dimensions derived from minimum rotated rectangle (Phase 2).
    # Added alongside the CREATE TABLE change so existing DBs gain them.
    "raw_ccgis_parcels": (
        ("width_ft", "REAL"),
        ("depth_ft", "REAL"),
    ),
    # Keep BOTH the largest-card and the summed-card values on the raw
    # characteristics row so we can compare 'assessor sum vs largest vs
    # footprint' per parcel without needing to refetch.
    "raw_assessor_characteristics": (
        ("char_bldg_sf_sum", "REAL"),
    ),
    # Skip-trace enrichment columns. dead/wrong_person are operator-set
    # tombstones surfaced from bounce polling or manual review; the rest
    # come from the enrichment provider (currently Tracerfy advanced mode).
    # related_person_name lets a single parcel carry multiple persons from
    # one advanced-mode lookup (officer/manager + owner, etc.).
    "contacts": (
        ("dead", "INTEGER DEFAULT 0"),
        ("wrong_person", "INTEGER DEFAULT 0"),
        ("confidence_pct", "INTEGER"),
        ("enrichment_source", "TEXT"),
        ("related_person_name", "TEXT"),
        ("dead_at", "TEXT"),
        ("dead_reason", "TEXT"),
    ),
}


def init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = get_connection(db_path)
    try:
        # WAL lets the enrichment orchestrator (background thread) write
        # while the Flask review UI reads without blocking. Persists on the
        # DB file once set, but cheap to re-assert on every init.
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA_SQL)
        for table, columns in _LATER_COLUMNS.items():
            for col, sql_type in columns:
                try:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {sql_type}")
                except sqlite3.OperationalError as e:
                    if "duplicate column" not in str(e).lower():
                        raise
        # Partial unique index on outreach(pin, touch_number). Partial WHERE
        # Per-recipient sends: each address in to_list gets its own outreach
        # row, so uniqueness now keys on (pin, touch_number, contact_id) —
        # multiple rows per touch (one per recipient) are valid; only
        # double-sending the same touch to the same contact is forbidden.
        # The old (pin, touch_number) index is dropped if present.
        conn.execute("DROP INDEX IF EXISTS idx_outreach_pin_touch_unique")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_outreach_pin_touch_contact_unique "
            "ON outreach(pin, touch_number, contact_id) "
            "WHERE touch_number IS NOT NULL AND contact_id IS NOT NULL"
        )
        # ---- Skip-trace enrichment tables (Plan 2026-05-23) ----
        # Order matters: enrichment_results FKs enrichment_jobs(id), so the
        # jobs table is created first. Indexes on enrichment_results follow
        # immediately after enrichment_results is defined.
        # Batch job: a set of pins submitted to the enrichment orchestrator.
        # pin_list_json holds the original input set so we can resume after
        # restarts; per-pin progress lives in enrichment_job_pins.
        # created_at/completed_at default to ISO-8601-with-Z to match the
        # project convention (see pipeline/cadence.py, outreach code).
        conn.execute("""
            CREATE TABLE IF NOT EXISTS enrichment_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pin_list_json TEXT NOT NULL,
                status TEXT NOT NULL,
                paused_reason TEXT,
                total_cost_usd REAL DEFAULT 0.0,
                created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                completed_at TEXT
            )
        """)
        # One row per provider call. raw_response_json preserved verbatim
        # so we can backfill new fields without re-paying for lookups.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS enrichment_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pin TEXT NOT NULL,
                job_id INTEGER,
                provider TEXT NOT NULL,
                lookup_type TEXT NOT NULL,
                query_name TEXT NOT NULL,
                query_mail_address TEXT,
                raw_response_json TEXT NOT NULL,
                cost_usd REAL NOT NULL,
                status TEXT NOT NULL,
                error_message TEXT,
                created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                FOREIGN KEY(pin) REFERENCES parcels(pin),
                FOREIGN KEY(job_id) REFERENCES enrichment_jobs(id)
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_enrichment_results_pin "
            "ON enrichment_results(pin)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_enrichment_results_job "
            "ON enrichment_results(job_id)"
        )
        conn.execute("""
            CREATE TABLE IF NOT EXISTS enrichment_job_pins (
                job_id INTEGER NOT NULL,
                pin TEXT NOT NULL,
                status TEXT NOT NULL,
                error_message TEXT,
                PRIMARY KEY (job_id, pin),
                FOREIGN KEY(job_id) REFERENCES enrichment_jobs(id),
                FOREIGN KEY(pin) REFERENCES parcels(pin)
            )
        """)
        # Singleton row holding the Gmail bounce poller's resume cursor.
        # CHECK (id = 1) makes the row-count invariant enforced by the DB,
        # not just convention.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bounce_poll_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                last_message_id TEXT,
                last_polled_at TEXT
            )
        """)
        conn.execute("INSERT OR IGNORE INTO bounce_poll_state(id) VALUES (1)")
        conn.commit()
    finally:
        conn.close()


def upsert_rows(
    db_path: Path,
    table: str,
    rows: Iterable[dict],
    key_columns: list[str],
    preserve_columns: list[str] | None = None,
) -> int:
    """
    INSERT ... ON CONFLICT(key_columns) DO UPDATE SET ...
    Each row is a dict; keys must match column names.
    Columns in preserve_columns are inserted on first write but NOT overwritten
    on conflict (useful for first_seen_date-style fields).
    Returns number of rows processed.
    """
    rows = list(rows)
    if not rows:
        return 0

    preserve = set(preserve_columns or [])
    columns = list(rows[0].keys())
    placeholders = ", ".join(f":{c}" for c in columns)
    col_list = ", ".join(columns)
    update_assignments = ", ".join(
        f"{c}=excluded.{c}" for c in columns if c not in key_columns and c not in preserve
    )
    conflict_cols = ", ".join(key_columns)

    if update_assignments:
        sql = (
            f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) "
            f"ON CONFLICT({conflict_cols}) DO UPDATE SET {update_assignments}"
        )
    else:
        sql = (
            f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) "
            f"ON CONFLICT({conflict_cols}) DO NOTHING"
        )

    conn = get_connection(db_path)
    try:
        conn.executemany(sql, rows)
        conn.commit()
    finally:
        conn.close()
    return len(rows)
