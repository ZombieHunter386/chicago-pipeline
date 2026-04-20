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
    allows_multifamily_by_right INTEGER,
    tif_district TEXT,
    cta_nearest_station TEXT,
    cta_distance_ft REAL,
    -- Scoring (populated in Plan 2)
    score REAL,
    score_version TEXT,
    consolidation_group_id INTEGER,
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

-- ============================================================
-- Consolidation groups — adjacent same-owner parcels
-- ============================================================
CREATE TABLE IF NOT EXISTS consolidation_groups (
    group_id INTEGER PRIMARY KEY AUTOINCREMENT,
    pins TEXT NOT NULL,                  -- JSON array of PINs
    combined_lot_size_sf REAL,
    owner_name TEXT,
    detected_date TEXT
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

-- Source 3A: Cook County Clerk — Delinquent Property Tax
CREATE TABLE IF NOT EXISTS raw_clerk_delinquent (
    pin TEXT PRIMARY KEY,
    delinquent_years INTEGER,
    earliest_delinquent_year INTEGER,
    total_owed REAL,
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


def init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = get_connection(db_path)
    try:
        conn.executescript(SCHEMA_SQL)
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
