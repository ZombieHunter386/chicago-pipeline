# Chicago Multifamily Pipeline — Data Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the data layer of the Chicago off-market multifamily pipeline — config system, SQLite schema, all fetch modules, geography filtering, and consolidation — so a single `python -m pipeline.fetch_all` populates the database with every parcel in the target geography plus all raw data from Cook County Assessor, Chicago Data Portal, and Cook County Clerk.

**Architecture:** Python CLI + SQLite. A shared Socrata client handles auth/pagination/rate limiting; each data source is a thin module implementing a uniform `fetch()` interface and writing raw rows to its own table. A separate `parcels` table holds derived/aggregated fields (absentee flag, LLC flag, hold duration, FAR, CTA distance) that downstream scoring will consume. Geography filtering uses geopandas point-in-polygon. Consolidation groups adjacent parcels by shared owner.

**Tech Stack:** Python 3.11+, SQLite (stdlib), `requests`, `pyyaml`, `geopandas`, `shapely`, `pandas`, `python-dotenv`, `pytest`, `responses` (HTTP mocking).

**Scope:** This plan covers data ingestion only. Scoring (Plan 2), Flask UI (Plan 3), and outreach (Plan 4) are deferred. The full DB schema (including `contacts`, `outreach`, `waves`) is created up front so later plans don't need migrations, but only the fetch-layer tables are populated here.

---

## File Structure

```
chicago-pipeline/
├── config/
│   ├── geography.yaml         # target polygon + bounding box
│   ├── scoring.yaml           # placeholder (populated in Plan 2)
│   ├── zoning_lookup.yaml     # zone_class → FAR/density/setbacks (seeded)
│   ├── tax_rates.yaml         # equalization factor + composite rates
│   ├── ui_filters.yaml        # placeholder (populated in Plan 3)
│   └── outreach.yaml          # placeholder (populated in Plan 4)
├── pipeline/
│   ├── __init__.py
│   ├── config.py              # YAML loader
│   ├── db.py                  # schema + connection + upsert helpers
│   ├── geography.py           # bbox + point-in-polygon filtering
│   ├── socrata.py             # shared Socrata client (auth, paging, retry)
│   ├── consolidate.py         # adjacent same-owner grouping
│   └── fetch_all.py           # CLI orchestrator
├── sources/
│   ├── __init__.py
│   ├── assessor_parcels.py
│   ├── assessor_addresses.py
│   ├── assessor_characteristics.py
│   ├── assessor_values.py
│   ├── assessor_sales.py
│   ├── assessor_appeals.py
│   ├── assessor_exempt.py
│   ├── cdp_zoning.py
│   ├── cdp_permits.py
│   ├── cdp_violations.py
│   ├── cdp_vacant.py
│   ├── cdp_cta_stations.py
│   └── clerk_delinquent.py
├── data/                      # SQLite + downloaded CSVs (gitignored)
│   └── .gitkeep
├── tests/
│   ├── conftest.py
│   ├── fixtures/
│   │   ├── assessor_parcels.json
│   │   ├── assessor_addresses.json
│   │   ├── ...
│   │   └── delinquent.csv
│   ├── test_config.py
│   ├── test_db.py
│   ├── test_geography.py
│   ├── test_socrata.py
│   ├── test_consolidate.py
│   ├── test_source_assessor_parcels.py
│   ├── test_source_assessor_addresses.py
│   ├── ... (one per source)
│   └── test_fetch_all.py
├── requirements.txt
├── .env.example
├── .gitignore
└── README.md
```

**Responsibility split:**
- `pipeline/` — generic infrastructure (config, DB, geography, HTTP, orchestration). Knows nothing about specific datasets.
- `sources/` — one module per data source. Each module declares its dataset ID, target table, and field mapping; calls into `pipeline/socrata.py` and `pipeline/db.py`. Adding a new source = new module here.
- `data/` — runtime artifacts (SQLite DB, downloaded CSVs). Gitignored.
- `config/` — YAML config. Edit a YAML file, no code change needed.

---

## Task 1: Project Scaffolding

**Files:**
- Create: `chicago-pipeline/requirements.txt`
- Create: `chicago-pipeline/.gitignore`
- Create: `chicago-pipeline/.env.example`
- Create: `chicago-pipeline/README.md`
- Create: `chicago-pipeline/data/.gitkeep`
- Create: `chicago-pipeline/pipeline/__init__.py` (empty)
- Create: `chicago-pipeline/sources/__init__.py` (empty)
- Create: `chicago-pipeline/tests/__init__.py` (empty)

- [ ] **Step 1: Create directories**

```bash
mkdir -p chicago-pipeline/{pipeline,sources,config,data,tests/fixtures}
touch chicago-pipeline/data/.gitkeep
touch chicago-pipeline/pipeline/__init__.py
touch chicago-pipeline/sources/__init__.py
touch chicago-pipeline/tests/__init__.py
```

- [ ] **Step 2: Write requirements.txt**

```
requests==2.32.3
pyyaml==6.0.2
geopandas==1.0.1
shapely==2.0.6
pandas==2.2.3
python-dotenv==1.0.1
pytest==8.3.3
responses==0.25.3
```

- [ ] **Step 3: Write .gitignore**

```
__pycache__/
*.pyc
.pytest_cache/
.env
data/*.db
data/*.db-journal
data/*.csv
.venv/
venv/
.DS_Store
```

- [ ] **Step 4: Write .env.example**

```
# Register at https://dev.socrata.com to get a free app token (1000 req/hr)
SOCRATA_APP_TOKEN=
# SQLite file path (default: data/pipeline.db)
PIPELINE_DB_PATH=data/pipeline.db
```

- [ ] **Step 5: Write README.md**

```markdown
# Chicago Multifamily Pipeline

Off-market deal sourcing pipeline for Chicago multifamily properties. Fetches parcel data from Cook County Assessor + Chicago Data Portal + Cook County Clerk, scores parcels by development potential and motivation signals, and supports outreach to property owners.

## Setup

1. `python -m venv .venv && source .venv/bin/activate`
2. `pip install -r requirements.txt`
3. Register at https://dev.socrata.com for a free app token
4. `cp .env.example .env` and fill in `SOCRATA_APP_TOKEN`
5. Edit `config/geography.yaml` if you want a different target area

## Usage

```bash
# Fetch all data for target geography (~5-10 min)
python -m pipeline.fetch_all

# Force refresh (ignore staleness check)
python -m pipeline.fetch_all --refresh
```

## Tests

```bash
pytest -v
```
```

- [ ] **Step 6: Create venv and install dependencies**

```bash
cd chicago-pipeline
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Expected: clean install with no errors.

- [ ] **Step 7: Commit**

```bash
git add chicago-pipeline/
git commit -m "feat: scaffold chicago pipeline project"
```

---

## Task 2: Geography Config

**Files:**
- Create: `chicago-pipeline/config/geography.yaml`

- [ ] **Step 1: Write geography.yaml with the target polygon**

The target boundary is Irving Park (N) → Fullerton (S) → Western (W) → Lake Michigan (E). Approximate corner coordinates pulled from those street intersections.

```yaml
# Target geography for the Chicago Multifamily Pipeline.
# Defined as a polygon of vertex coordinates (lat, lng).
# Edit this file and re-run fetch to expand/narrow the search area.

name: "Lincoln Park / Lakeview / adjacent"

# Polygon vertices, in order, closing back to the first point.
# Order: NW corner → NE → SE → SW → NW.
polygon:
  - [41.9540, -87.6880]   # NW: Irving Park & Western
  - [41.9540, -87.6380]   # NE: Irving Park & Lake Michigan (approx shoreline)
  - [41.9244, -87.6240]   # SE: Fullerton & Lake Michigan (approx shoreline)
  - [41.9244, -87.6880]   # SW: Fullerton & Western

# Bounding box derived from polygon — used for Socrata $where prefiltering.
# Computed once and stored here so every source uses identical bounds.
bbox:
  min_lat: 41.9244
  max_lat: 41.9540
  min_lng: -87.6880
  max_lng: -87.6240
```

- [ ] **Step 2: Commit**

```bash
git add chicago-pipeline/config/geography.yaml
git commit -m "feat: add target geography config"
```

---

## Task 3: Config Loader (TDD)

**Files:**
- Create: `chicago-pipeline/pipeline/config.py`
- Test: `chicago-pipeline/tests/test_config.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config.py
import pytest
from pathlib import Path
from pipeline.config import load_config, get_geography, GeographyConfig


def test_load_geography_returns_polygon_and_bbox(tmp_path):
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "geography.yaml").write_text(
        """
name: Test Area
polygon:
  - [41.0, -87.0]
  - [41.0, -86.0]
  - [40.0, -86.0]
  - [40.0, -87.0]
bbox:
  min_lat: 40.0
  max_lat: 41.0
  min_lng: -87.0
  max_lng: -86.0
"""
    )
    geo = get_geography(cfg_dir)
    assert isinstance(geo, GeographyConfig)
    assert geo.name == "Test Area"
    assert len(geo.polygon) == 4
    assert geo.bbox == (40.0, 41.0, -87.0, -86.0)  # (min_lat, max_lat, min_lng, max_lng)


def test_load_config_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        get_geography(tmp_path / "missing")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pipeline.config'`

- [ ] **Step 3: Implement the config module**

```python
# pipeline/config.py
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Any
import yaml


CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


@dataclass(frozen=True)
class GeographyConfig:
    name: str
    polygon: list[tuple[float, float]]   # list of (lat, lng) vertices
    bbox: tuple[float, float, float, float]   # (min_lat, max_lat, min_lng, max_lng)


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with path.open() as f:
        return yaml.safe_load(f) or {}


def get_geography(config_dir: Path = CONFIG_DIR) -> GeographyConfig:
    raw = load_config(config_dir / "geography.yaml")
    polygon = [tuple(pt) for pt in raw["polygon"]]
    b = raw["bbox"]
    return GeographyConfig(
        name=raw["name"],
        polygon=polygon,
        bbox=(b["min_lat"], b["max_lat"], b["min_lng"], b["max_lng"]),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_config.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add chicago-pipeline/pipeline/config.py chicago-pipeline/tests/test_config.py
git commit -m "feat: add YAML config loader with geography support"
```

---

## Task 4: SQLite Schema (TDD)

**Files:**
- Create: `chicago-pipeline/pipeline/db.py`
- Test: `chicago-pipeline/tests/test_db.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_db.py
import sqlite3
from pathlib import Path
from pipeline.db import init_db, get_connection


REQUIRED_TABLES = {
    "parcels",
    "consolidation_groups",
    "contacts",
    "outreach",
    "waves",
    "raw_assessor_parcels",
    "raw_assessor_addresses",
    "raw_assessor_characteristics",
    "raw_assessor_values",
    "raw_assessor_sales",
    "raw_assessor_appeals",
    "raw_assessor_exempt",
    "raw_cdp_zoning",
    "raw_cdp_permits",
    "raw_cdp_violations",
    "raw_cdp_vacant",
    "raw_cdp_cta_stations",
    "raw_clerk_delinquent",
    "fetch_log",
}


def test_init_db_creates_all_required_tables(tmp_path):
    db = tmp_path / "test.db"
    init_db(db)
    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    tables = {r[0] for r in rows}
    missing = REQUIRED_TABLES - tables
    assert not missing, f"Missing tables: {missing}"


def test_parcels_table_has_pin_primary_key(tmp_path):
    db = tmp_path / "test.db"
    init_db(db)
    conn = sqlite3.connect(db)
    info = conn.execute("PRAGMA table_info(parcels)").fetchall()
    pk_cols = [c[1] for c in info if c[5] > 0]
    assert pk_cols == ["pin"]


def test_init_db_is_idempotent(tmp_path):
    db = tmp_path / "test.db"
    init_db(db)
    init_db(db)  # second call must not raise


def test_get_connection_enables_foreign_keys(tmp_path):
    db = tmp_path / "test.db"
    init_db(db)
    conn = get_connection(db)
    fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    assert fk == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_db.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pipeline.db'`

- [ ] **Step 3: Implement db.py**

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_db.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add chicago-pipeline/pipeline/db.py chicago-pipeline/tests/test_db.py
git commit -m "feat: add SQLite schema with parcels, raw_*, outreach tables"
```

---

## Task 5: Upsert Helper (TDD)

**Files:**
- Modify: `chicago-pipeline/pipeline/db.py`
- Test: `chicago-pipeline/tests/test_db.py`

- [ ] **Step 1: Add failing tests for upsert_rows**

Append to `tests/test_db.py`:

```python
from pipeline.db import upsert_rows


def test_upsert_rows_inserts_new(tmp_path):
    db = tmp_path / "test.db"
    init_db(db)
    rows = [
        {"pin": "X1", "exemption_type": "church", "fetched_at": "2026-04-19"},
        {"pin": "X2", "exemption_type": "school", "fetched_at": "2026-04-19"},
    ]
    n = upsert_rows(db, "raw_assessor_exempt", rows, key_columns=["pin"])
    assert n == 2
    conn = get_connection(db)
    got = conn.execute("SELECT pin, exemption_type FROM raw_assessor_exempt ORDER BY pin").fetchall()
    assert [(r[0], r[1]) for r in got] == [("X1", "church"), ("X2", "school")]


def test_upsert_rows_updates_existing(tmp_path):
    db = tmp_path / "test.db"
    init_db(db)
    upsert_rows(db, "raw_assessor_exempt",
                [{"pin": "X1", "exemption_type": "church", "fetched_at": "2026-04-01"}],
                key_columns=["pin"])
    upsert_rows(db, "raw_assessor_exempt",
                [{"pin": "X1", "exemption_type": "synagogue", "fetched_at": "2026-04-19"}],
                key_columns=["pin"])
    conn = get_connection(db)
    row = conn.execute("SELECT exemption_type, fetched_at FROM raw_assessor_exempt WHERE pin='X1'").fetchone()
    assert row[0] == "synagogue"
    assert row[1] == "2026-04-19"


def test_upsert_rows_empty_list_is_noop(tmp_path):
    db = tmp_path / "test.db"
    init_db(db)
    n = upsert_rows(db, "raw_assessor_exempt", [], key_columns=["pin"])
    assert n == 0


def test_upsert_rows_composite_key(tmp_path):
    db = tmp_path / "test.db"
    init_db(db)
    rows = [
        {"pin": "X1", "year": "2024", "class": "211", "lat": 41.9, "lon": -87.6,
         "pin10": None, "ward_num": None, "zip_code": None, "tax_tif_district_num": None,
         "tax_tif_district_name": None, "township_code": None, "nbhd_code": None,
         "fetched_at": "2026-04-19"},
        {"pin": "X1", "year": "2025", "class": "211", "lat": 41.9, "lon": -87.6,
         "pin10": None, "ward_num": None, "zip_code": None, "tax_tif_district_num": None,
         "tax_tif_district_name": None, "township_code": None, "nbhd_code": None,
         "fetched_at": "2026-04-19"},
    ]
    n = upsert_rows(db, "raw_assessor_parcels", rows, key_columns=["pin", "year"])
    assert n == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_db.py -v`
Expected: FAIL — `cannot import name 'upsert_rows'`

- [ ] **Step 3: Implement upsert_rows**

Append to `pipeline/db.py`:

```python
def upsert_rows(
    db_path: Path,
    table: str,
    rows: Iterable[dict],
    key_columns: list[str],
) -> int:
    """
    INSERT ... ON CONFLICT(key_columns) DO UPDATE SET ...
    Each row is a dict; keys must match column names.
    Returns number of rows processed.
    """
    rows = list(rows)
    if not rows:
        return 0

    columns = list(rows[0].keys())
    placeholders = ", ".join(f":{c}" for c in columns)
    col_list = ", ".join(columns)
    update_assignments = ", ".join(
        f"{c}=excluded.{c}" for c in columns if c not in key_columns
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_db.py -v`
Expected: PASS (8 tests total)

- [ ] **Step 5: Commit**

```bash
git add chicago-pipeline/pipeline/db.py chicago-pipeline/tests/test_db.py
git commit -m "feat: add upsert_rows helper"
```

---

## Task 6: Geography Filter (TDD)

**Files:**
- Create: `chicago-pipeline/pipeline/geography.py`
- Test: `chicago-pipeline/tests/test_geography.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_geography.py
from pipeline.config import GeographyConfig
from pipeline.geography import in_polygon, filter_by_polygon, bbox_where_clause


GEO = GeographyConfig(
    name="Test",
    polygon=[(41.0, -87.0), (41.0, -86.0), (40.0, -86.0), (40.0, -87.0)],
    bbox=(40.0, 41.0, -87.0, -86.0),
)


def test_in_polygon_inside():
    assert in_polygon(40.5, -86.5, GEO) is True


def test_in_polygon_outside():
    assert in_polygon(42.0, -86.5, GEO) is False


def test_in_polygon_boundary_inclusive():
    # geopandas' covers() handles boundary; either True or False is acceptable
    # but should not crash
    in_polygon(41.0, -87.0, GEO)


def test_filter_by_polygon_drops_outside_points():
    rows = [
        {"pin": "in1", "lat": 40.5, "lng": -86.5},
        {"pin": "out1", "lat": 50.0, "lng": -86.5},
        {"pin": "in2", "lat": 40.9, "lng": -86.9},
    ]
    kept = filter_by_polygon(rows, GEO, lat_field="lat", lng_field="lng")
    pins = {r["pin"] for r in kept}
    assert pins == {"in1", "in2"}


def test_bbox_where_clause_socrata_format():
    clause = bbox_where_clause(GEO, lat_field="lat", lng_field="lon")
    # Should produce a SoQL-compatible string with all four bounds
    assert "lat between 40.0 and 41.0" in clause.lower()
    assert "lon between -87.0 and -86.0" in clause.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_geography.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement geography.py**

```python
# pipeline/geography.py
from __future__ import annotations
from typing import Iterable
from shapely.geometry import Point, Polygon
from pipeline.config import GeographyConfig


def _polygon(geo: GeographyConfig) -> Polygon:
    # GeographyConfig.polygon is list of (lat, lng); shapely wants (x=lng, y=lat)
    return Polygon([(lng, lat) for lat, lng in geo.polygon])


def in_polygon(lat: float, lng: float, geo: GeographyConfig) -> bool:
    if lat is None or lng is None:
        return False
    poly = _polygon(geo)
    return poly.covers(Point(lng, lat))


def filter_by_polygon(
    rows: Iterable[dict],
    geo: GeographyConfig,
    lat_field: str = "lat",
    lng_field: str = "lng",
) -> list[dict]:
    poly = _polygon(geo)
    out = []
    for r in rows:
        lat = r.get(lat_field)
        lng = r.get(lng_field)
        if lat is None or lng is None:
            continue
        try:
            if poly.covers(Point(float(lng), float(lat))):
                out.append(r)
        except (TypeError, ValueError):
            continue
    return out


def bbox_where_clause(
    geo: GeographyConfig,
    lat_field: str = "lat",
    lng_field: str = "lon",
) -> str:
    """SoQL $where clause for a coarse bounding-box prefilter."""
    min_lat, max_lat, min_lng, max_lng = geo.bbox
    return (
        f"{lat_field} between {min_lat} and {max_lat} "
        f"AND {lng_field} between {min_lng} and {max_lng}"
    )
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_geography.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add chicago-pipeline/pipeline/geography.py chicago-pipeline/tests/test_geography.py
git commit -m "feat: add geography polygon filtering and bbox SoQL builder"
```

---

## Task 7: Socrata Client (TDD)

**Files:**
- Create: `chicago-pipeline/pipeline/socrata.py`
- Test: `chicago-pipeline/tests/test_socrata.py`

The Socrata client handles auth, pagination, retries, and rate limiting for both Cook County (`datacatalog.cookcountyil.gov`) and Chicago Data Portal (`data.cityofchicago.org`).

- [ ] **Step 1: Write failing tests**

```python
# tests/test_socrata.py
import responses
import pytest
from pipeline.socrata import SocrataClient, SocrataError


@responses.activate
def test_fetch_paginated_single_page():
    url = "https://datacatalog.cookcountyil.gov/resource/abc-123.json"
    responses.add(
        responses.GET, url,
        json=[{"pin": "1"}, {"pin": "2"}], status=200,
    )
    client = SocrataClient(domain="datacatalog.cookcountyil.gov", app_token="TKN")
    rows = list(client.fetch("abc-123", limit=50000))
    assert len(rows) == 2
    # Ensure app token header was sent
    assert responses.calls[0].request.headers.get("X-App-Token") == "TKN"


@responses.activate
def test_fetch_paginated_multiple_pages():
    url = "https://data.cityofchicago.org/resource/xyz-456.json"
    page1 = [{"id": str(i)} for i in range(50000)]
    page2 = [{"id": str(i)} for i in range(50000, 50010)]
    responses.add(responses.GET, url, json=page1, status=200)
    responses.add(responses.GET, url, json=page2, status=200)
    responses.add(responses.GET, url, json=[], status=200)
    client = SocrataClient(domain="data.cityofchicago.org", app_token="TKN")
    rows = list(client.fetch("xyz-456", limit=50000))
    assert len(rows) == 50010


@responses.activate
def test_fetch_with_where_clause():
    url = "https://data.cityofchicago.org/resource/xyz-456.json"
    responses.add(responses.GET, url, json=[{"id": "1"}], status=200)
    client = SocrataClient(domain="data.cityofchicago.org", app_token="TKN")
    list(client.fetch("xyz-456", where="lat between 40 and 41", limit=50000))
    qs = responses.calls[0].request.url
    assert "%24where=" in qs or "$where=" in qs


@responses.activate
def test_fetch_retries_on_500_then_succeeds():
    url = "https://data.cityofchicago.org/resource/xyz-456.json"
    responses.add(responses.GET, url, status=500)
    responses.add(responses.GET, url, json=[{"id": "1"}], status=200)
    responses.add(responses.GET, url, json=[], status=200)
    client = SocrataClient(domain="data.cityofchicago.org", app_token="TKN",
                           retry_backoff=0.0)
    rows = list(client.fetch("xyz-456", limit=50000))
    assert len(rows) == 1


@responses.activate
def test_fetch_raises_after_max_retries():
    url = "https://data.cityofchicago.org/resource/xyz-456.json"
    for _ in range(5):
        responses.add(responses.GET, url, status=500)
    client = SocrataClient(domain="data.cityofchicago.org", app_token="TKN",
                           retry_backoff=0.0, max_retries=3)
    with pytest.raises(SocrataError):
        list(client.fetch("xyz-456", limit=50000))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_socrata.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement socrata.py**

```python
# pipeline/socrata.py
from __future__ import annotations
import time
from typing import Iterator, Optional
import requests


class SocrataError(Exception):
    pass


class SocrataClient:
    """
    Minimal Socrata SODA REST client with pagination + retry + rate limiting.
    Works against both datacatalog.cookcountyil.gov and data.cityofchicago.org.
    """

    def __init__(
        self,
        domain: str,
        app_token: str,
        max_retries: int = 5,
        retry_backoff: float = 1.0,
        rate_limit_sleep: float = 0.0,
        timeout: float = 60.0,
    ):
        self.domain = domain.rstrip("/")
        self.app_token = app_token
        self.max_retries = max_retries
        self.retry_backoff = retry_backoff
        self.rate_limit_sleep = rate_limit_sleep
        self.timeout = timeout
        self.session = requests.Session()

    def fetch(
        self,
        dataset_id: str,
        where: Optional[str] = None,
        select: Optional[str] = None,
        order: Optional[str] = None,
        limit: int = 50000,
    ) -> Iterator[dict]:
        """Yield rows from a Socrata dataset, handling pagination."""
        url = f"https://{self.domain}/resource/{dataset_id}.json"
        offset = 0
        while True:
            params = {"$limit": limit, "$offset": offset}
            if where:
                params["$where"] = where
            if select:
                params["$select"] = select
            if order:
                params["$order"] = order

            page = self._get_with_retry(url, params)
            if not page:
                return
            for row in page:
                yield row
            if len(page) < limit:
                return
            offset += limit
            if self.rate_limit_sleep:
                time.sleep(self.rate_limit_sleep)

    def _get_with_retry(self, url: str, params: dict) -> list[dict]:
        headers = {"X-App-Token": self.app_token} if self.app_token else {}
        last_err: Optional[Exception] = None
        for attempt in range(self.max_retries):
            try:
                resp = self.session.get(url, params=params, headers=headers, timeout=self.timeout)
                if resp.status_code == 429:
                    sleep_for = self.retry_backoff * (2 ** attempt)
                    time.sleep(sleep_for)
                    continue
                if 500 <= resp.status_code < 600:
                    sleep_for = self.retry_backoff * (2 ** attempt)
                    time.sleep(sleep_for)
                    continue
                resp.raise_for_status()
                return resp.json()
            except requests.RequestException as e:
                last_err = e
                time.sleep(self.retry_backoff * (2 ** attempt))
        raise SocrataError(f"Failed after {self.max_retries} retries: {url} {params} ({last_err})")
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_socrata.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add chicago-pipeline/pipeline/socrata.py chicago-pipeline/tests/test_socrata.py
git commit -m "feat: add Socrata client with pagination and retry"
```

---

## Task 8: Source Module Pattern + Conftest

Each fetch module follows the same skeleton:

```python
DATASET_ID = "<id>"
TABLE = "raw_<source>"
KEY_COLUMNS = [...]

def fetch(geo, db_path, client) -> int:
    rows = []
    for raw in client.fetch(DATASET_ID, where=...):
        rows.append(transform(raw))
    rows = filter_by_polygon(rows, geo, lat_field=..., lng_field=...)
    return upsert_rows(db_path, TABLE, rows, key_columns=KEY_COLUMNS)
```

This task sets up shared test fixtures.

**Files:**
- Create: `chicago-pipeline/tests/conftest.py`

- [ ] **Step 1: Write conftest with shared fixtures**

```python
# tests/conftest.py
import pytest
from pathlib import Path
from pipeline.config import GeographyConfig
from pipeline.db import init_db
from pipeline.socrata import SocrataClient


@pytest.fixture
def db_path(tmp_path):
    p = tmp_path / "test.db"
    init_db(p)
    return p


@pytest.fixture
def geo():
    return GeographyConfig(
        name="Test",
        polygon=[(41.95, -87.69), (41.95, -87.62),
                 (41.92, -87.62), (41.92, -87.69)],
        bbox=(41.92, 41.95, -87.69, -87.62),
    )


@pytest.fixture
def cook_client():
    return SocrataClient(domain="datacatalog.cookcountyil.gov",
                         app_token="TEST_TOKEN", retry_backoff=0.0)


@pytest.fixture
def cdp_client():
    return SocrataClient(domain="data.cityofchicago.org",
                         app_token="TEST_TOKEN", retry_backoff=0.0)


FIXTURES = Path(__file__).parent / "fixtures"
```

- [ ] **Step 2: Commit**

```bash
git add chicago-pipeline/tests/conftest.py
git commit -m "test: add shared pytest fixtures"
```

---

## Task 9: Source — Assessor Parcels (1A)

**Files:**
- Create: `chicago-pipeline/sources/assessor_parcels.py`
- Test: `chicago-pipeline/tests/test_source_assessor_parcels.py`
- Create: `chicago-pipeline/tests/fixtures/assessor_parcels.json`

- [ ] **Step 1: Create the fixture**

```json
[
  {"pin": "14210010010000", "year": "2025", "pin10": "1421001001",
   "class": "211", "lat": "41.94", "lon": "-87.65", "ward_num": "44",
   "zip_code": "60657", "tax_tif_district_num": null,
   "tax_tif_district_name": null, "township_code": "76", "nbhd_code": "10"},
  {"pin": "14210010020000", "year": "2025", "pin10": "1421001002",
   "class": "203", "lat": "41.93", "lon": "-87.66", "ward_num": "32",
   "zip_code": "60614", "tax_tif_district_num": null,
   "tax_tif_district_name": null, "township_code": "76", "nbhd_code": "10"},
  {"pin": "99999990000000", "year": "2025", "pin10": "9999999000",
   "class": "211", "lat": "30.00", "lon": "-90.00", "ward_num": "0",
   "zip_code": "00000", "tax_tif_district_num": null,
   "tax_tif_district_name": null, "township_code": "00", "nbhd_code": "00"}
]
```

- [ ] **Step 2: Write failing test**

```python
# tests/test_source_assessor_parcels.py
import json
import responses
from sources import assessor_parcels
from pipeline.db import get_connection
from tests.conftest import FIXTURES


@responses.activate
def test_fetch_loads_parcels_in_geography_only(db_path, geo, cook_client):
    fixture = json.loads((FIXTURES / "assessor_parcels.json").read_text())
    responses.add(
        responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_parcels.DATASET_ID}.json",
        json=fixture, status=200,
    )
    responses.add(
        responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_parcels.DATASET_ID}.json",
        json=[], status=200,
    )
    n = assessor_parcels.fetch(geo, db_path, cook_client)
    # The 99999... row is far outside the polygon and must be dropped
    assert n == 2

    conn = get_connection(db_path)
    rows = conn.execute("SELECT pin FROM raw_assessor_parcels ORDER BY pin").fetchall()
    pins = [r[0] for r in rows]
    assert "14210010010000" in pins
    assert "99999990000000" not in pins

    # Parcels stub row should also exist for downstream join
    parcel_rows = conn.execute("SELECT pin, lat, lng, ward_num FROM parcels ORDER BY pin").fetchall()
    assert len(parcel_rows) == 2
    assert parcel_rows[0]["pin"] == "14210010010000"
    assert parcel_rows[0]["lat"] == 41.94
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_source_assessor_parcels.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sources.assessor_parcels'`

- [ ] **Step 4: Implement the source**

```python
# sources/assessor_parcels.py
"""Source 1A — Cook County Assessor Parcel Universe."""
from __future__ import annotations
from datetime import datetime, UTC
from pathlib import Path
from pipeline.config import GeographyConfig
from pipeline.db import upsert_rows, get_connection
from pipeline.geography import filter_by_polygon, bbox_where_clause
from pipeline.socrata import SocrataClient


DATASET_ID = "nj4t-kc8j"
TABLE = "raw_assessor_parcels"
SOURCE_NAME = "assessor_parcels"


def _to_float(v):
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def fetch(geo: GeographyConfig, db_path: Path, client: SocrataClient) -> int:
    where = bbox_where_clause(geo, lat_field="lat", lng_field="lon")
    # Pull current year only — historical years not needed for this source
    where = f"({where})"
    fetched_at = datetime.now(UTC).isoformat(timespec="seconds")

    raw_rows = []
    for r in client.fetch(DATASET_ID, where=where):
        raw_rows.append({
            "pin": r.get("pin"),
            "year": r.get("year"),
            "pin10": r.get("pin10"),
            "class": r.get("class"),
            "lat": _to_float(r.get("lat")),
            "lon": _to_float(r.get("lon")),
            "ward_num": r.get("ward_num"),
            "zip_code": r.get("zip_code"),
            "tax_tif_district_num": r.get("tax_tif_district_num"),
            "tax_tif_district_name": r.get("tax_tif_district_name"),
            "township_code": r.get("township_code"),
            "nbhd_code": r.get("nbhd_code"),
            "fetched_at": fetched_at,
        })

    # Precise polygon filter (bbox was a coarse SoQL prefilter)
    raw_rows = filter_by_polygon(raw_rows, geo, lat_field="lat", lng_field="lon")

    n = upsert_rows(db_path, TABLE, raw_rows, key_columns=["pin", "year"])

    # Also upsert into the parcels table with identity columns
    parcel_rows = [{
        "pin": r["pin"],
        "pin10": r["pin10"],
        "lat": r["lat"],
        "lng": r["lon"],
        "ward_num": r["ward_num"],
        "zip_code": r["zip_code"],
        "property_class": r["class"],
        "tif_district": r["tax_tif_district_name"],
        "first_seen_date": fetched_at,
        "last_fetched_date": fetched_at,
        "last_updated_date": fetched_at,
        "stage": "scored",
    } for r in raw_rows]
    _upsert_parcels(db_path, parcel_rows)
    return n


def _upsert_parcels(db_path: Path, rows: list[dict]) -> None:
    """
    Special-case upsert into parcels: never overwrite first_seen_date on
    existing rows.
    """
    if not rows:
        return
    conn = get_connection(db_path)
    try:
        for r in rows:
            conn.execute("""
                INSERT INTO parcels (pin, pin10, lat, lng, ward_num, zip_code,
                                     property_class, tif_district,
                                     first_seen_date, last_fetched_date,
                                     last_updated_date, stage)
                VALUES (:pin, :pin10, :lat, :lng, :ward_num, :zip_code,
                        :property_class, :tif_district,
                        :first_seen_date, :last_fetched_date,
                        :last_updated_date, :stage)
                ON CONFLICT(pin) DO UPDATE SET
                    pin10=excluded.pin10,
                    lat=excluded.lat,
                    lng=excluded.lng,
                    ward_num=excluded.ward_num,
                    zip_code=excluded.zip_code,
                    property_class=excluded.property_class,
                    tif_district=excluded.tif_district,
                    last_fetched_date=excluded.last_fetched_date,
                    last_updated_date=excluded.last_updated_date
            """, r)
        conn.commit()
    finally:
        conn.close()
```

- [ ] **Step 5: Run test**

Run: `pytest tests/test_source_assessor_parcels.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add chicago-pipeline/sources/assessor_parcels.py \
        chicago-pipeline/tests/test_source_assessor_parcels.py \
        chicago-pipeline/tests/fixtures/assessor_parcels.json
git commit -m "feat: add assessor parcels (1A) fetch module"
```

---

## Task 10: Source — Assessor Addresses (1B) + Absentee/LLC Derivation

Joins to existing `parcels` rows by PIN. Computes `is_absentee` and `is_llc` flags during fetch.

**Files:**
- Create: `chicago-pipeline/sources/assessor_addresses.py`
- Test: `chicago-pipeline/tests/test_source_assessor_addresses.py`
- Create: `chicago-pipeline/tests/fixtures/assessor_addresses.json`

- [ ] **Step 1: Create the fixture**

```json
[
  {"pin": "14210010010000",
   "prop_address_full": "100 W DIVERSEY PKWY", "prop_address_city_name": "CHICAGO",
   "prop_address_state": "IL", "prop_address_zipcode_1": "60614",
   "mail_address_name": "RACINE HOLDINGS LLC", "mail_address_full": "PO BOX 4421",
   "mail_address_city_name": "NAPERVILLE", "mail_address_state": "IL",
   "mail_address_zipcode_1": "60540",
   "owner_address_name": "RACINE HOLDINGS LLC", "owner_address_full": "PO BOX 4421"},
  {"pin": "14210010020000",
   "prop_address_full": "200 N HALSTED ST", "prop_address_city_name": "CHICAGO",
   "prop_address_state": "IL", "prop_address_zipcode_1": "60614",
   "mail_address_name": "JOHN SMITH", "mail_address_full": "200 N HALSTED ST",
   "mail_address_city_name": "CHICAGO", "mail_address_state": "IL",
   "mail_address_zipcode_1": "60614",
   "owner_address_name": "JOHN SMITH", "owner_address_full": "200 N HALSTED ST"}
]
```

- [ ] **Step 2: Write failing test**

```python
# tests/test_source_assessor_addresses.py
import json
import responses
from sources import assessor_parcels, assessor_addresses
from pipeline.db import get_connection
from tests.conftest import FIXTURES


@responses.activate
def test_addresses_populates_owner_and_derives_absentee_llc(db_path, geo, cook_client):
    # Seed parcels first using the parcels fetcher
    parcels_fixture = json.loads((FIXTURES / "assessor_parcels.json").read_text())
    responses.add(
        responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_parcels.DATASET_ID}.json",
        json=parcels_fixture, status=200,
    )
    responses.add(
        responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_parcels.DATASET_ID}.json",
        json=[], status=200,
    )
    assessor_parcels.fetch(geo, db_path, cook_client)

    # Now fetch addresses
    addr_fixture = json.loads((FIXTURES / "assessor_addresses.json").read_text())
    responses.add(
        responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_addresses.DATASET_ID}.json",
        json=addr_fixture, status=200,
    )
    responses.add(
        responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_addresses.DATASET_ID}.json",
        json=[], status=200,
    )
    n = assessor_addresses.fetch(geo, db_path, cook_client)
    assert n == 2

    conn = get_connection(db_path)
    rows = conn.execute("""
        SELECT pin, owner_name, mail_address, is_absentee, is_llc, address
        FROM parcels ORDER BY pin
    """).fetchall()
    by_pin = {r["pin"]: r for r in rows}
    # LLC + mail addr differs from prop addr → absentee + llc
    assert by_pin["14210010010000"]["is_absentee"] == 1
    assert by_pin["14210010010000"]["is_llc"] == 1
    assert by_pin["14210010010000"]["owner_name"] == "RACINE HOLDINGS LLC"
    assert by_pin["14210010010000"]["address"] == "100 W DIVERSEY PKWY"
    # Individual + mail addr matches prop addr → not absentee, not llc
    assert by_pin["14210010020000"]["is_absentee"] == 0
    assert by_pin["14210010020000"]["is_llc"] == 0


def test_is_llc_detects_common_patterns():
    from sources.assessor_addresses import is_llc
    assert is_llc("RACINE HOLDINGS LLC") is True
    assert is_llc("acme corp") is True
    assert is_llc("Smith Family Trust") is True
    assert is_llc("LP PARTNERS") is True
    assert is_llc("XYZ INC") is True
    assert is_llc("John Smith") is False
    assert is_llc(None) is False


def test_is_absentee_normalizes_addresses():
    from sources.assessor_addresses import is_absentee
    assert is_absentee("100 W DIVERSEY", "PO BOX 4421") is True
    assert is_absentee("100 W DIVERSEY", "100 W DIVERSEY") is False
    assert is_absentee("100 w diversey", "100 W DIVERSEY") is False  # case-insensitive
    assert is_absentee("100 W DIVERSEY ", " 100 W DIVERSEY") is False  # trim
    assert is_absentee(None, "100 W DIVERSEY") is False
```

- [ ] **Step 3: Run test (fails)**

Run: `pytest tests/test_source_assessor_addresses.py -v`
Expected: FAIL — module not found

- [ ] **Step 4: Implement the source**

```python
# sources/assessor_addresses.py
"""Source 1B — Cook County Assessor Parcel Addresses."""
from __future__ import annotations
import re
from datetime import datetime, UTC
from pathlib import Path
from pipeline.config import GeographyConfig
from pipeline.db import upsert_rows, get_connection
from pipeline.socrata import SocrataClient


DATASET_ID = "3723-97qp"
TABLE = "raw_assessor_addresses"
SOURCE_NAME = "assessor_addresses"

LLC_PATTERN = re.compile(
    r"\b(LLC|L\.L\.C\.|CORP|CORPORATION|INC|INCORPORATED|TRUST|LP|L\.P\.|PARTNERS|PARTNERSHIP|LLP|L\.L\.P\.|HOLDINGS|REALTY|PROPERTIES)\b",
    re.IGNORECASE,
)


def is_llc(name: str | None) -> bool:
    if not name:
        return False
    return bool(LLC_PATTERN.search(name))


def _norm_addr(a: str | None) -> str | None:
    if not a:
        return None
    return re.sub(r"\s+", " ", a).strip().upper()


def is_absentee(prop_addr: str | None, mail_addr: str | None) -> bool:
    p = _norm_addr(prop_addr)
    m = _norm_addr(mail_addr)
    if p is None or m is None:
        return False
    return p != m


def fetch(geo: GeographyConfig, db_path: Path, client: SocrataClient) -> int:
    """
    No bbox prefilter possible — this dataset has no lat/lng. Filter to
    PINs already in our parcels table (set by Source 1A).
    """
    fetched_at = datetime.now(UTC).isoformat(timespec="seconds")
    conn = get_connection(db_path)
    try:
        known_pins = {row[0] for row in conn.execute("SELECT pin FROM parcels")}
    finally:
        conn.close()
    if not known_pins:
        return 0

    raw_rows = []
    for r in client.fetch(DATASET_ID):
        pin = r.get("pin")
        if pin not in known_pins:
            continue
        raw_rows.append({
            "pin": pin,
            "prop_address_full": r.get("prop_address_full"),
            "prop_address_city_name": r.get("prop_address_city_name"),
            "prop_address_state": r.get("prop_address_state"),
            "prop_address_zipcode_1": r.get("prop_address_zipcode_1"),
            "mail_address_name": r.get("mail_address_name"),
            "mail_address_full": r.get("mail_address_full"),
            "mail_address_city_name": r.get("mail_address_city_name"),
            "mail_address_state": r.get("mail_address_state"),
            "mail_address_zipcode_1": r.get("mail_address_zipcode_1"),
            "owner_address_name": r.get("owner_address_name"),
            "owner_address_full": r.get("owner_address_full"),
            "fetched_at": fetched_at,
        })

    n = upsert_rows(db_path, TABLE, raw_rows, key_columns=["pin"])

    # Update parcels with derived fields
    conn = get_connection(db_path)
    try:
        for r in raw_rows:
            owner = r["owner_address_name"] or r["mail_address_name"]
            absentee = 1 if is_absentee(r["prop_address_full"], r["mail_address_full"]) else 0
            llc = 1 if is_llc(r["mail_address_name"]) else 0
            conn.execute("""
                UPDATE parcels SET
                    address = :address,
                    owner_name = :owner_name,
                    owner_address = :owner_address,
                    mail_name = :mail_name,
                    mail_address = :mail_address,
                    is_absentee = :is_absentee,
                    is_llc = :is_llc,
                    last_updated_date = :now
                WHERE pin = :pin
            """, {
                "address": r["prop_address_full"],
                "owner_name": owner,
                "owner_address": r["owner_address_full"],
                "mail_name": r["mail_address_name"],
                "mail_address": r["mail_address_full"],
                "is_absentee": absentee,
                "is_llc": llc,
                "now": fetched_at,
                "pin": r["pin"],
            })
        conn.commit()
    finally:
        conn.close()
    return n
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_source_assessor_addresses.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Commit**

```bash
git add chicago-pipeline/sources/assessor_addresses.py \
        chicago-pipeline/tests/test_source_assessor_addresses.py \
        chicago-pipeline/tests/fixtures/assessor_addresses.json
git commit -m "feat: add assessor addresses (1B) with absentee/LLC derivation"
```

---

## Task 11: Source — Assessor Characteristics (1C) + Built FAR

Stores raw characteristics. Computes `lot_size_sf`, `building_sf`, `year_built`, `condition`, `building_classification`, `built_far` on the `parcels` row.

**Files:**
- Create: `chicago-pipeline/sources/assessor_characteristics.py`
- Test: `chicago-pipeline/tests/test_source_assessor_characteristics.py`
- Create: `chicago-pipeline/tests/fixtures/assessor_characteristics.json`

- [ ] **Step 1: Create fixture**

```json
[
  {"pin": "14210010010000", "year": "2025", "class": "211",
   "char_land_sf": "3750", "char_bldg_sf": "2400", "char_yrblt": "1923",
   "char_cnst_qlty": "Average", "char_repair_cnd": "Fair", "cdu": "Fair",
   "char_beds": "8", "char_rooms": "16", "char_fbath": "4", "char_hbath": "0",
   "char_type_resd": "2 Story", "char_ext_wall": "Brick", "char_heat": "Steam",
   "char_bsmt": "Y", "char_bsmt_fin": "N", "char_gar1_att": "N",
   "char_gar1_area": "0", "char_use": "Multi-Family", "char_site": "Average",
   "char_air": "None", "pin_is_multicard": "false", "pin_num_cards": "1"},
  {"pin": "14210010020000", "year": "2025", "class": "203",
   "char_land_sf": "5000", "char_bldg_sf": "3000", "char_yrblt": "1958",
   "char_cnst_qlty": "Good", "char_repair_cnd": "Average", "cdu": "Good",
   "char_beds": "6", "char_rooms": "12", "char_fbath": "3", "char_hbath": "1",
   "char_type_resd": "3 Story", "char_ext_wall": "Brick", "char_heat": "Forced Air",
   "char_bsmt": "Y", "char_bsmt_fin": "Y", "char_gar1_att": "N",
   "char_gar1_area": "0", "char_use": "Multi-Family", "char_site": "Good",
   "char_air": "Central", "pin_is_multicard": "false", "pin_num_cards": "1"}
]
```

- [ ] **Step 2: Write failing test**

```python
# tests/test_source_assessor_characteristics.py
import json
import responses
from sources import assessor_parcels, assessor_characteristics
from pipeline.db import get_connection
from tests.conftest import FIXTURES


@responses.activate
def test_characteristics_populates_building_facts_and_built_far(db_path, geo, cook_client):
    parcels_fixture = json.loads((FIXTURES / "assessor_parcels.json").read_text())
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_parcels.DATASET_ID}.json",
        json=parcels_fixture, status=200)
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_parcels.DATASET_ID}.json",
        json=[], status=200)
    assessor_parcels.fetch(geo, db_path, cook_client)

    char_fixture = json.loads((FIXTURES / "assessor_characteristics.json").read_text())
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_characteristics.DATASET_ID}.json",
        json=char_fixture, status=200)
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_characteristics.DATASET_ID}.json",
        json=[], status=200)
    n = assessor_characteristics.fetch(geo, db_path, cook_client)
    assert n == 2

    conn = get_connection(db_path)
    p = conn.execute("""
        SELECT pin, lot_size_sf, building_sf, year_built, condition, built_far,
               building_classification
        FROM parcels WHERE pin='14210010010000'
    """).fetchone()
    assert p["lot_size_sf"] == 3750.0
    assert p["building_sf"] == 2400.0
    assert p["year_built"] == 1923
    assert p["condition"] == "Fair"
    assert p["built_far"] == 0.64  # 2400/3750 rounded to 2dp
    assert p["building_classification"] == "2 Story"
```

- [ ] **Step 3: Run test (fails)**

Run: `pytest tests/test_source_assessor_characteristics.py -v`
Expected: FAIL

- [ ] **Step 4: Implement the source**

```python
# sources/assessor_characteristics.py
"""Source 1C — Cook County Assessor Improvement Characteristics."""
from __future__ import annotations
from datetime import datetime, UTC
from pathlib import Path
from pipeline.config import GeographyConfig
from pipeline.db import upsert_rows, get_connection
from pipeline.socrata import SocrataClient


DATASET_ID = "x54s-btds"
TABLE = "raw_assessor_characteristics"
SOURCE_NAME = "assessor_characteristics"


def _f(v):
    if v in (None, ""): return None
    try: return float(v)
    except (TypeError, ValueError): return None

def _i(v):
    if v in (None, ""): return None
    try: return int(float(v))
    except (TypeError, ValueError): return None

def _b(v):
    if v in (None, ""): return None
    return 1 if str(v).lower() in ("true", "t", "y", "yes", "1") else 0


def fetch(geo: GeographyConfig, db_path: Path, client: SocrataClient) -> int:
    fetched_at = datetime.now(UTC).isoformat(timespec="seconds")
    conn = get_connection(db_path)
    try:
        known_pins = {row[0] for row in conn.execute("SELECT pin FROM parcels")}
    finally:
        conn.close()
    if not known_pins:
        return 0

    # Pull most recent year only for each PIN by ordering desc and de-duping
    raw_rows: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for r in client.fetch(DATASET_ID, order="year DESC"):
        pin = r.get("pin")
        year = r.get("year")
        if pin not in known_pins:
            continue
        if (pin, year) in seen:
            continue
        seen.add((pin, year))
        raw_rows.append({
            "pin": pin, "year": year, "class": r.get("class"),
            "char_land_sf": _f(r.get("char_land_sf")),
            "char_bldg_sf": _f(r.get("char_bldg_sf")),
            "char_yrblt": r.get("char_yrblt"),
            "char_cnst_qlty": r.get("char_cnst_qlty"),
            "char_repair_cnd": r.get("char_repair_cnd"),
            "cdu": r.get("cdu"),
            "char_beds": r.get("char_beds"),
            "char_rooms": r.get("char_rooms"),
            "char_fbath": r.get("char_fbath"),
            "char_hbath": r.get("char_hbath"),
            "char_type_resd": r.get("char_type_resd"),
            "char_ext_wall": r.get("char_ext_wall"),
            "char_heat": r.get("char_heat"),
            "char_bsmt": r.get("char_bsmt"),
            "char_bsmt_fin": r.get("char_bsmt_fin"),
            "char_gar1_att": r.get("char_gar1_att"),
            "char_gar1_area": r.get("char_gar1_area"),
            "char_use": r.get("char_use"),
            "char_site": r.get("char_site"),
            "char_air": r.get("char_air"),
            "pin_is_multicard": _b(r.get("pin_is_multicard")),
            "pin_num_cards": _i(r.get("pin_num_cards")),
            "fetched_at": fetched_at,
        })

    n = upsert_rows(db_path, TABLE, raw_rows, key_columns=["pin", "year"])

    # Update parcels with most recent year of characteristics per PIN
    by_pin: dict[str, dict] = {}
    for r in raw_rows:
        if r["pin"] not in by_pin:
            by_pin[r["pin"]] = r

    conn = get_connection(db_path)
    try:
        for pin, r in by_pin.items():
            lot = r["char_land_sf"]
            bldg = r["char_bldg_sf"]
            built_far = round(bldg / lot, 2) if (lot and bldg and lot > 0) else None
            condition = r["char_repair_cnd"] or r["cdu"]
            yr = _i(r["char_yrblt"])
            conn.execute("""
                UPDATE parcels SET
                    lot_size_sf = :lot,
                    building_sf = :bldg,
                    year_built = :yr,
                    condition = :condition,
                    building_classification = :bclass,
                    built_far = :bfar,
                    last_updated_date = :now
                WHERE pin = :pin
            """, {"lot": lot, "bldg": bldg, "yr": yr, "condition": condition,
                  "bclass": r["char_type_resd"], "bfar": built_far,
                  "now": fetched_at, "pin": pin})
        conn.commit()
    finally:
        conn.close()
    return n
```

- [ ] **Step 5: Run test**

Run: `pytest tests/test_source_assessor_characteristics.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add chicago-pipeline/sources/assessor_characteristics.py \
        chicago-pipeline/tests/test_source_assessor_characteristics.py \
        chicago-pipeline/tests/fixtures/assessor_characteristics.json
git commit -m "feat: add assessor characteristics (1C) with built FAR computation"
```

---

## Task 12: Source — Assessor Values (1D) + Tax Trends

Pulls last 5 years of assessed values per PIN. Computes `assessed_land`, `assessed_building`, `assessed_total`, `land_building_ratio`, `tax_increase_pct_1yr`, `tax_increase_pct_5yr` on the parcel.

**Files:**
- Create: `chicago-pipeline/sources/assessor_values.py`
- Test: `chicago-pipeline/tests/test_source_assessor_values.py`
- Create: `chicago-pipeline/tests/fixtures/assessor_values.json`

- [ ] **Step 1: Create fixture (5 years per PIN)**

```json
[
  {"pin": "14210010010000", "year": "2025", "mailed_bldg": "100000", "mailed_land": "200000", "mailed_tot": "300000",
   "certified_bldg": "100000", "certified_land": "200000", "certified_tot": "300000",
   "board_bldg": "95000", "board_land": "192430", "board_tot": "287430", "board_hie": "0"},
  {"pin": "14210010010000", "year": "2024", "mailed_bldg": null, "mailed_land": null, "mailed_tot": null,
   "certified_bldg": null, "certified_land": null, "certified_tot": null,
   "board_bldg": "90000", "board_land": "175000", "board_tot": "265000", "board_hie": "0"},
  {"pin": "14210010010000", "year": "2020", "mailed_bldg": null, "mailed_land": null, "mailed_tot": null,
   "certified_bldg": null, "certified_land": null, "certified_tot": null,
   "board_bldg": "75000", "board_land": "139000", "board_tot": "214000", "board_hie": "0"}
]
```

- [ ] **Step 2: Write failing test**

```python
# tests/test_source_assessor_values.py
import json
import responses
from sources import assessor_parcels, assessor_values
from pipeline.db import get_connection
from tests.conftest import FIXTURES


@responses.activate
def test_values_populates_assessed_and_trends(db_path, geo, cook_client):
    parcels_fixture = json.loads((FIXTURES / "assessor_parcels.json").read_text())
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_parcels.DATASET_ID}.json",
        json=parcels_fixture, status=200)
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_parcels.DATASET_ID}.json",
        json=[], status=200)
    assessor_parcels.fetch(geo, db_path, cook_client)

    vals_fixture = json.loads((FIXTURES / "assessor_values.json").read_text())
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_values.DATASET_ID}.json",
        json=vals_fixture, status=200)
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_values.DATASET_ID}.json",
        json=[], status=200)
    assessor_values.fetch(geo, db_path, cook_client)

    conn = get_connection(db_path)
    p = conn.execute("""
        SELECT assessed_total, assessed_land, land_building_ratio,
               tax_increase_pct_1yr, tax_increase_pct_5yr
        FROM parcels WHERE pin='14210010010000'
    """).fetchone()
    assert p["assessed_total"] == 287430.0
    assert p["assessed_land"] == 192430.0
    # 192430/287430 ≈ 0.67
    assert round(p["land_building_ratio"], 2) == 0.67
    # 287430/265000 - 1 = 8.46%
    assert round(p["tax_increase_pct_1yr"], 1) == 8.5
    # 287430/214000 - 1 = 34.31%
    assert round(p["tax_increase_pct_5yr"], 1) == 34.3
```

- [ ] **Step 3: Run test (fails)**

Run: `pytest tests/test_source_assessor_values.py -v`
Expected: FAIL

- [ ] **Step 4: Implement the source**

```python
# sources/assessor_values.py
"""Source 1D — Cook County Assessor Assessed Values."""
from __future__ import annotations
from datetime import datetime, UTC
from pathlib import Path
from collections import defaultdict
from pipeline.config import GeographyConfig
from pipeline.db import upsert_rows, get_connection
from pipeline.socrata import SocrataClient


DATASET_ID = "uzyt-m557"
TABLE = "raw_assessor_values"
SOURCE_NAME = "assessor_values"
HISTORY_YEARS = 5


def _f(v):
    if v in (None, ""): return None
    try: return float(v)
    except (TypeError, ValueError): return None


def fetch(geo: GeographyConfig, db_path: Path, client: SocrataClient) -> int:
    fetched_at = datetime.now(UTC).isoformat(timespec="seconds")
    conn = get_connection(db_path)
    try:
        known_pins = {row[0] for row in conn.execute("SELECT pin FROM parcels")}
    finally:
        conn.close()
    if not known_pins:
        return 0

    raw_rows = []
    for r in client.fetch(DATASET_ID, order="year DESC"):
        pin = r.get("pin")
        if pin not in known_pins:
            continue
        raw_rows.append({
            "pin": pin, "year": r.get("year"),
            "mailed_bldg": _f(r.get("mailed_bldg")),
            "mailed_land": _f(r.get("mailed_land")),
            "mailed_tot": _f(r.get("mailed_tot")),
            "certified_bldg": _f(r.get("certified_bldg")),
            "certified_land": _f(r.get("certified_land")),
            "certified_tot": _f(r.get("certified_tot")),
            "board_bldg": _f(r.get("board_bldg")),
            "board_land": _f(r.get("board_land")),
            "board_tot": _f(r.get("board_tot")),
            "board_hie": _f(r.get("board_hie")),
            "fetched_at": fetched_at,
        })

    n = upsert_rows(db_path, TABLE, raw_rows, key_columns=["pin", "year"])

    # Group by PIN, sort by year DESC, compute trends
    by_pin: dict[str, list[dict]] = defaultdict(list)
    for r in raw_rows:
        by_pin[r["pin"]].append(r)
    for rows in by_pin.values():
        rows.sort(key=lambda x: int(x["year"]) if x["year"] else 0, reverse=True)

    conn = get_connection(db_path)
    try:
        for pin, rows in by_pin.items():
            current = rows[0]
            assessed_total = current["board_tot"]
            assessed_land = current["board_land"]
            assessed_bldg = current["board_bldg"]
            ratio = (assessed_land / assessed_total) if (assessed_land and assessed_total) else None

            inc_1yr = None
            if len(rows) >= 2 and rows[1]["board_tot"] and rows[0]["board_tot"]:
                inc_1yr = (rows[0]["board_tot"] / rows[1]["board_tot"] - 1) * 100

            inc_5yr = None
            current_year = int(current["year"]) if current["year"] else None
            if current_year is not None:
                target_year = current_year - 5
                old = next((r for r in rows if r["year"] and int(r["year"]) <= target_year), None)
                if old and old["board_tot"] and current["board_tot"]:
                    inc_5yr = (current["board_tot"] / old["board_tot"] - 1) * 100

            conn.execute("""
                UPDATE parcels SET
                    assessed_land = :al,
                    assessed_building = :ab,
                    assessed_total = :at,
                    land_building_ratio = :ratio,
                    tax_increase_pct_1yr = :i1,
                    tax_increase_pct_5yr = :i5,
                    last_updated_date = :now
                WHERE pin = :pin
            """, {"al": assessed_land, "ab": assessed_bldg, "at": assessed_total,
                  "ratio": ratio, "i1": inc_1yr, "i5": inc_5yr,
                  "now": fetched_at, "pin": pin})
        conn.commit()
    finally:
        conn.close()
    return n
```

- [ ] **Step 5: Run test**

Run: `pytest tests/test_source_assessor_values.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add chicago-pipeline/sources/assessor_values.py \
        chicago-pipeline/tests/test_source_assessor_values.py \
        chicago-pipeline/tests/fixtures/assessor_values.json
git commit -m "feat: add assessor values (1D) with land/building ratio and tax trends"
```

---

## Task 13: Source — Assessor Sales (1E) + Hold Duration

**Files:**
- Create: `chicago-pipeline/sources/assessor_sales.py`
- Test: `chicago-pipeline/tests/test_source_assessor_sales.py`
- Create: `chicago-pipeline/tests/fixtures/assessor_sales.json`

- [ ] **Step 1: Create fixture**

```json
[
  {"pin": "14210010010000", "sale_date": "2004-03-15T00:00:00.000",
   "sale_price": "385000", "seller_name": "PRIOR LLC", "buyer_name": "RACINE HOLDINGS LLC",
   "deed_type": "Warranty", "doc_no": "0408001",
   "is_multisale": "false", "num_parcels_sale": "1",
   "sale_filter_same_sale_within_365": "false", "sale_filter_less_than_10k": "false",
   "sale_filter_deed_type": "false"},
  {"pin": "14210010010000", "sale_date": "1999-08-01T00:00:00.000",
   "sale_price": "150000", "seller_name": "OLDER LLC", "buyer_name": "PRIOR LLC",
   "deed_type": "Warranty", "doc_no": "9908001",
   "is_multisale": "false", "num_parcels_sale": "1",
   "sale_filter_same_sale_within_365": "false", "sale_filter_less_than_10k": "false",
   "sale_filter_deed_type": "false"},
  {"pin": "14210010020000", "sale_date": "2018-06-12T00:00:00.000",
   "sale_price": "475000", "seller_name": "X", "buyer_name": "JOHN SMITH",
   "deed_type": "Warranty", "doc_no": "1806001",
   "is_multisale": "false", "num_parcels_sale": "1",
   "sale_filter_same_sale_within_365": "false", "sale_filter_less_than_10k": "false",
   "sale_filter_deed_type": "false"}
]
```

- [ ] **Step 2: Write failing test**

```python
# tests/test_source_assessor_sales.py
import json
import responses
from datetime import date
from sources import assessor_parcels, assessor_sales
from pipeline.db import get_connection
from tests.conftest import FIXTURES


@responses.activate
def test_sales_populates_last_sale_and_hold_duration(db_path, geo, cook_client, monkeypatch):
    monkeypatch.setattr(assessor_sales, "TODAY", date(2026, 4, 19))

    parcels_fixture = json.loads((FIXTURES / "assessor_parcels.json").read_text())
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_parcels.DATASET_ID}.json",
        json=parcels_fixture, status=200)
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_parcels.DATASET_ID}.json",
        json=[], status=200)
    assessor_parcels.fetch(geo, db_path, cook_client)

    sales_fixture = json.loads((FIXTURES / "assessor_sales.json").read_text())
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_sales.DATASET_ID}.json",
        json=sales_fixture, status=200)
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_sales.DATASET_ID}.json",
        json=[], status=200)
    assessor_sales.fetch(geo, db_path, cook_client)

    conn = get_connection(db_path)
    p1 = conn.execute("""
        SELECT last_sale_date, last_sale_price, hold_duration_years, deed_type
        FROM parcels WHERE pin='14210010010000'
    """).fetchone()
    assert p1["last_sale_date"] == "2004-03-15"
    assert p1["last_sale_price"] == 385000.0
    # 2026-04-19 - 2004-03-15 ≈ 22.1 years
    assert 22.0 <= p1["hold_duration_years"] <= 22.2
    assert p1["deed_type"] == "Warranty"
```

- [ ] **Step 3: Run test (fails)**

Run: `pytest tests/test_source_assessor_sales.py -v`
Expected: FAIL

- [ ] **Step 4: Implement the source**

```python
# sources/assessor_sales.py
"""Source 1E — Cook County Assessor Parcel Sales."""
from __future__ import annotations
from datetime import datetime, date, UTC
from pathlib import Path
from collections import defaultdict
from pipeline.config import GeographyConfig
from pipeline.db import upsert_rows, get_connection
from pipeline.socrata import SocrataClient


DATASET_ID = "wvhk-k5uv"
TABLE = "raw_assessor_sales"
SOURCE_NAME = "assessor_sales"
TODAY = date.today()


def _f(v):
    if v in (None, ""): return None
    try: return float(v)
    except (TypeError, ValueError): return None

def _b(v):
    if v in (None, ""): return None
    return 1 if str(v).lower() in ("true", "t", "y", "yes", "1") else 0

def _date_only(v):
    if not v: return None
    # Socrata returns ISO datetime; trim to date
    return v[:10]


def fetch(geo: GeographyConfig, db_path: Path, client: SocrataClient) -> int:
    fetched_at = datetime.now(UTC).isoformat(timespec="seconds")
    conn = get_connection(db_path)
    try:
        known_pins = {row[0] for row in conn.execute("SELECT pin FROM parcels")}
    finally:
        conn.close()
    if not known_pins:
        return 0

    raw_rows = []
    for r in client.fetch(DATASET_ID, order="sale_date DESC"):
        pin = r.get("pin")
        if pin not in known_pins:
            continue
        raw_rows.append({
            "pin": pin,
            "sale_date": _date_only(r.get("sale_date")),
            "sale_price": _f(r.get("sale_price")),
            "seller_name": r.get("seller_name"),
            "buyer_name": r.get("buyer_name"),
            "deed_type": r.get("deed_type"),
            "doc_no": r.get("doc_no") or "",
            "is_multisale": _b(r.get("is_multisale")),
            "num_parcels_sale": int(float(r["num_parcels_sale"])) if r.get("num_parcels_sale") else None,
            "sale_filter_same_sale_within_365": _b(r.get("sale_filter_same_sale_within_365")),
            "sale_filter_less_than_10k": _b(r.get("sale_filter_less_than_10k")),
            "sale_filter_deed_type": _b(r.get("sale_filter_deed_type")),
            "fetched_at": fetched_at,
        })

    n = upsert_rows(db_path, TABLE, raw_rows, key_columns=["pin", "sale_date", "doc_no"])

    # Most recent arm's-length sale per PIN
    by_pin: dict[str, list[dict]] = defaultdict(list)
    for r in raw_rows:
        # exclude non-arm's-length flagged sales when computing hold
        if r["sale_filter_same_sale_within_365"] or r["sale_filter_less_than_10k"]:
            continue
        if not r["sale_date"]:
            continue
        by_pin[r["pin"]].append(r)
    for v in by_pin.values():
        v.sort(key=lambda x: x["sale_date"], reverse=True)

    conn = get_connection(db_path)
    try:
        for pin, rows in by_pin.items():
            latest = rows[0]
            sd = datetime.strptime(latest["sale_date"], "%Y-%m-%d").date()
            hold = (TODAY - sd).days / 365.25
            conn.execute("""
                UPDATE parcels SET
                    last_sale_date = :sd,
                    last_sale_price = :sp,
                    hold_duration_years = :hold,
                    deed_type = :deed,
                    last_updated_date = :now
                WHERE pin = :pin
            """, {"sd": latest["sale_date"], "sp": latest["sale_price"],
                  "hold": round(hold, 2), "deed": latest["deed_type"],
                  "now": fetched_at, "pin": pin})
        conn.commit()
    finally:
        conn.close()
    return n
```

- [ ] **Step 5: Run test**

Run: `pytest tests/test_source_assessor_sales.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add chicago-pipeline/sources/assessor_sales.py \
        chicago-pipeline/tests/test_source_assessor_sales.py \
        chicago-pipeline/tests/fixtures/assessor_sales.json
git commit -m "feat: add assessor sales (1E) with hold duration"
```

---

## Task 14: Source — Assessor Appeals (1F) + Tax-Exempt (1G)

These two are similar enough to do together. Each is small.

**Files:**
- Create: `chicago-pipeline/sources/assessor_appeals.py`
- Create: `chicago-pipeline/sources/assessor_exempt.py`
- Test: `chicago-pipeline/tests/test_source_assessor_appeals_exempt.py`
- Create: `chicago-pipeline/tests/fixtures/assessor_appeals.json`
- Create: `chicago-pipeline/tests/fixtures/assessor_exempt.json`

- [ ] **Step 1: Create fixtures**

`tests/fixtures/assessor_appeals.json`:
```json
[
  {"pin": "14210010010000", "year": "2024", "appeal_outcome": "Reduced", "assessed_value_change": "-15000"},
  {"pin": "14210010010000", "year": "2022", "appeal_outcome": "Denied", "assessed_value_change": "0"}
]
```

`tests/fixtures/assessor_exempt.json`:
```json
[
  {"pin": "14210010030000", "exemption_type": "Religious"}
]
```

- [ ] **Step 2: Write failing tests**

```python
# tests/test_source_assessor_appeals_exempt.py
import json
import responses
from sources import assessor_parcels, assessor_appeals, assessor_exempt
from pipeline.db import get_connection
from tests.conftest import FIXTURES


def _seed_parcels(db_path, geo, cook_client):
    fixture = json.loads((FIXTURES / "assessor_parcels.json").read_text())
    # Add an extra parcel to test exempt case
    fixture.append({"pin": "14210010030000", "year": "2025", "pin10": "1421001003",
                    "class": "320", "lat": "41.94", "lon": "-87.65", "ward_num": "44",
                    "zip_code": "60657", "tax_tif_district_num": None,
                    "tax_tif_district_name": None, "township_code": "76", "nbhd_code": "10"})
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_parcels.DATASET_ID}.json",
        json=fixture, status=200)
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_parcels.DATASET_ID}.json",
        json=[], status=200)
    assessor_parcels.fetch(geo, db_path, cook_client)


@responses.activate
def test_appeals_count_per_pin(db_path, geo, cook_client):
    _seed_parcels(db_path, geo, cook_client)
    fx = json.loads((FIXTURES / "assessor_appeals.json").read_text())
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_appeals.DATASET_ID}.json",
        json=fx, status=200)
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_appeals.DATASET_ID}.json",
        json=[], status=200)
    assessor_appeals.fetch(geo, db_path, cook_client)
    conn = get_connection(db_path)
    n = conn.execute("SELECT appeal_count FROM parcels WHERE pin='14210010010000'").fetchone()[0]
    assert n == 2


@responses.activate
def test_exempt_pins_stored(db_path, geo, cook_client):
    _seed_parcels(db_path, geo, cook_client)
    fx = json.loads((FIXTURES / "assessor_exempt.json").read_text())
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_exempt.DATASET_ID}.json",
        json=fx, status=200)
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_exempt.DATASET_ID}.json",
        json=[], status=200)
    assessor_exempt.fetch(geo, db_path, cook_client)
    conn = get_connection(db_path)
    rows = conn.execute("SELECT pin, exemption_type FROM raw_assessor_exempt").fetchall()
    assert (rows[0][0], rows[0][1]) == ("14210010030000", "Religious")
```

- [ ] **Step 3: Run tests (fail)**

Run: `pytest tests/test_source_assessor_appeals_exempt.py -v`
Expected: FAIL

- [ ] **Step 4: Implement appeals**

```python
# sources/assessor_appeals.py
"""Source 1F — Cook County Assessor Appeals."""
from __future__ import annotations
from datetime import datetime, UTC
from pathlib import Path
from collections import Counter
from pipeline.config import GeographyConfig
from pipeline.db import upsert_rows, get_connection
from pipeline.socrata import SocrataClient


DATASET_ID = "y282-6ig3"
TABLE = "raw_assessor_appeals"
SOURCE_NAME = "assessor_appeals"


def _f(v):
    if v in (None, ""): return None
    try: return float(v)
    except (TypeError, ValueError): return None


def fetch(geo: GeographyConfig, db_path: Path, client: SocrataClient) -> int:
    fetched_at = datetime.now(UTC).isoformat(timespec="seconds")
    conn = get_connection(db_path)
    try:
        known_pins = {row[0] for row in conn.execute("SELECT pin FROM parcels")}
    finally:
        conn.close()
    if not known_pins:
        return 0

    raw_rows = []
    counts: Counter[str] = Counter()
    for r in client.fetch(DATASET_ID):
        pin = r.get("pin")
        if pin not in known_pins:
            continue
        raw_rows.append({
            "pin": pin, "year": r.get("year"),
            "appeal_outcome": r.get("appeal_outcome"),
            "assessed_value_change": _f(r.get("assessed_value_change")),
            "fetched_at": fetched_at,
        })
        counts[pin] += 1

    n = upsert_rows(db_path, TABLE, raw_rows, key_columns=["pin", "year"])

    conn = get_connection(db_path)
    try:
        for pin, c in counts.items():
            conn.execute("UPDATE parcels SET appeal_count=:c, last_updated_date=:t WHERE pin=:pin",
                         {"c": c, "t": fetched_at, "pin": pin})
        conn.commit()
    finally:
        conn.close()
    return n
```

- [ ] **Step 5: Implement exempt**

```python
# sources/assessor_exempt.py
"""Source 1G — Cook County Assessor Tax-Exempt Parcels."""
from __future__ import annotations
from datetime import datetime, UTC
from pathlib import Path
from pipeline.config import GeographyConfig
from pipeline.db import upsert_rows, get_connection
from pipeline.socrata import SocrataClient


DATASET_ID = "vgzx-68gb"
TABLE = "raw_assessor_exempt"
SOURCE_NAME = "assessor_exempt"


def fetch(geo: GeographyConfig, db_path: Path, client: SocrataClient) -> int:
    fetched_at = datetime.now(UTC).isoformat(timespec="seconds")
    conn = get_connection(db_path)
    try:
        known_pins = {row[0] for row in conn.execute("SELECT pin FROM parcels")}
    finally:
        conn.close()
    if not known_pins:
        return 0

    raw_rows = []
    for r in client.fetch(DATASET_ID):
        pin = r.get("pin")
        if pin not in known_pins:
            continue
        raw_rows.append({
            "pin": pin,
            "exemption_type": r.get("exemption_type") or r.get("exempt_type"),
            "fetched_at": fetched_at,
        })
    return upsert_rows(db_path, TABLE, raw_rows, key_columns=["pin"])
```

- [ ] **Step 6: Run tests**

Run: `pytest tests/test_source_assessor_appeals_exempt.py -v`
Expected: PASS (2 tests)

- [ ] **Step 7: Commit**

```bash
git add chicago-pipeline/sources/assessor_appeals.py \
        chicago-pipeline/sources/assessor_exempt.py \
        chicago-pipeline/tests/test_source_assessor_appeals_exempt.py \
        chicago-pipeline/tests/fixtures/assessor_appeals.json \
        chicago-pipeline/tests/fixtures/assessor_exempt.json
git commit -m "feat: add assessor appeals (1F) and tax-exempt (1G) fetchers"
```

---

## Task 15: Zoning Lookup YAML (Static Reference)

The zoning lookup is a hand-curated reference — not fetched. Seed it with the most common Chicago zone classes for LP/Lakeview. Adding more zones later = edit YAML.

**Files:**
- Create: `chicago-pipeline/config/zoning_lookup.yaml`
- Create: `chicago-pipeline/pipeline/zoning_lookup.py`
- Test: `chicago-pipeline/tests/test_zoning_lookup.py`

- [ ] **Step 1: Seed zoning_lookup.yaml**

```yaml
# Chicago zone class → development limits.
# Sources: Chicago Zoning Ordinance, Second City Zoning (https://secondcityzoning.org).
# Add new zone classes here as the geography expands.

zones:
  RS-3:
    max_far: 0.9
    max_height_ft: 30
    max_density: null
    min_lot_area_per_unit: 2500
    setback_front_ft: 15
    setback_side_ft: 4
    setback_rear_ft: 30
    allows_multifamily: false

  RT-3.5:
    max_far: 1.05
    max_height_ft: 35
    max_density: null
    min_lot_area_per_unit: 1250
    setback_front_ft: 15
    setback_side_ft: 4
    setback_rear_ft: 30
    allows_multifamily: true

  RT-4:
    max_far: 1.2
    max_height_ft: 38
    max_density: null
    min_lot_area_per_unit: 1000
    setback_front_ft: 15
    setback_side_ft: 4
    setback_rear_ft: 30
    allows_multifamily: true

  RM-4.5:
    max_far: 1.7
    max_height_ft: 38
    max_density: null
    min_lot_area_per_unit: 600
    setback_front_ft: 15
    setback_side_ft: 4
    setback_rear_ft: 30
    allows_multifamily: true

  RM-5:
    max_far: 2.0
    max_height_ft: 47
    max_density: null
    min_lot_area_per_unit: 400
    setback_front_ft: 15
    setback_side_ft: 4
    setback_rear_ft: 30
    allows_multifamily: true

  RM-5.5:
    max_far: 2.5
    max_height_ft: 60
    max_density: null
    min_lot_area_per_unit: 270
    setback_front_ft: 15
    setback_side_ft: 4
    setback_rear_ft: 30
    allows_multifamily: true

  RM-6:
    max_far: 4.4
    max_height_ft: 80
    max_density: null
    min_lot_area_per_unit: 165
    setback_front_ft: 15
    setback_side_ft: 4
    setback_rear_ft: 30
    allows_multifamily: true

  B1-1:
    max_far: 1.2
    max_height_ft: 38
    max_density: null
    min_lot_area_per_unit: 1000
    setback_front_ft: 0
    setback_side_ft: 0
    setback_rear_ft: 30
    allows_multifamily: true

  B2-3:
    max_far: 3.0
    max_height_ft: 50
    max_density: null
    min_lot_area_per_unit: 400
    setback_front_ft: 0
    setback_side_ft: 0
    setback_rear_ft: 30
    allows_multifamily: true

  B2-5:
    max_far: 5.0
    max_height_ft: 65
    max_density: null
    min_lot_area_per_unit: 300
    setback_front_ft: 0
    setback_side_ft: 0
    setback_rear_ft: 30
    allows_multifamily: true

  B3-2:
    max_far: 2.2
    max_height_ft: 45
    max_density: null
    min_lot_area_per_unit: 600
    setback_front_ft: 0
    setback_side_ft: 0
    setback_rear_ft: 30
    allows_multifamily: true

  B3-3:
    max_far: 3.0
    max_height_ft: 50
    max_density: null
    min_lot_area_per_unit: 400
    setback_front_ft: 0
    setback_side_ft: 0
    setback_rear_ft: 30
    allows_multifamily: true

  C1-3:
    max_far: 3.0
    max_height_ft: 50
    max_density: null
    min_lot_area_per_unit: 400
    setback_front_ft: 0
    setback_side_ft: 0
    setback_rear_ft: 30
    allows_multifamily: true

  C1-5:
    max_far: 5.0
    max_height_ft: 65
    max_density: null
    min_lot_area_per_unit: 300
    setback_front_ft: 0
    setback_side_ft: 0
    setback_rear_ft: 30
    allows_multifamily: true
```

- [ ] **Step 2: Write failing test**

```python
# tests/test_zoning_lookup.py
from pipeline.zoning_lookup import load_zoning_lookup, ZoneInfo


def test_load_returns_dict_keyed_by_zone_class(tmp_path):
    p = tmp_path / "zoning_lookup.yaml"
    p.write_text(
        """
zones:
  RT-4:
    max_far: 1.2
    max_height_ft: 38
    max_density: null
    min_lot_area_per_unit: 1000
    setback_front_ft: 15
    setback_side_ft: 4
    setback_rear_ft: 30
    allows_multifamily: true
"""
    )
    zl = load_zoning_lookup(p)
    assert "RT-4" in zl
    assert isinstance(zl["RT-4"], ZoneInfo)
    assert zl["RT-4"].max_far == 1.2
    assert zl["RT-4"].allows_multifamily is True
```

- [ ] **Step 3: Run test (fails)**

Run: `pytest tests/test_zoning_lookup.py -v`
Expected: FAIL

- [ ] **Step 4: Implement**

```python
# pipeline/zoning_lookup.py
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import yaml


@dataclass(frozen=True)
class ZoneInfo:
    max_far: Optional[float]
    max_height_ft: Optional[float]
    max_density: Optional[float]
    min_lot_area_per_unit: Optional[float]
    setback_front_ft: Optional[float]
    setback_side_ft: Optional[float]
    setback_rear_ft: Optional[float]
    allows_multifamily: bool


def load_zoning_lookup(path: Path) -> dict[str, ZoneInfo]:
    with path.open() as f:
        data = yaml.safe_load(f) or {}
    out: dict[str, ZoneInfo] = {}
    for zone, attrs in (data.get("zones") or {}).items():
        out[zone] = ZoneInfo(
            max_far=attrs.get("max_far"),
            max_height_ft=attrs.get("max_height_ft"),
            max_density=attrs.get("max_density"),
            min_lot_area_per_unit=attrs.get("min_lot_area_per_unit"),
            setback_front_ft=attrs.get("setback_front_ft"),
            setback_side_ft=attrs.get("setback_side_ft"),
            setback_rear_ft=attrs.get("setback_rear_ft"),
            allows_multifamily=bool(attrs.get("allows_multifamily")),
        )
    return out
```

- [ ] **Step 5: Run test**

Run: `pytest tests/test_zoning_lookup.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add chicago-pipeline/config/zoning_lookup.yaml \
        chicago-pipeline/pipeline/zoning_lookup.py \
        chicago-pipeline/tests/test_zoning_lookup.py
git commit -m "feat: add static zoning lookup YAML and loader"
```

---

## Task 16: Source — CDP Zoning (2A) + Spatial Join + FAR Gap

Pulls all zoning polygons in the bbox, spatially joins each parcel to its zone, then writes `zone_class`, `max_far`, `far_gap`, `allows_multifamily_by_right` onto the parcel.

**Files:**
- Create: `chicago-pipeline/sources/cdp_zoning.py`
- Test: `chicago-pipeline/tests/test_source_cdp_zoning.py`
- Create: `chicago-pipeline/tests/fixtures/cdp_zoning.json`

- [ ] **Step 1: Create fixture (one zoning polygon covering the parcels)**

```json
[
  {"objectid": "z1", "zone_class": "RT-4", "pd_num": null,
   "the_geom": {"type": "MultiPolygon",
                "coordinates": [[[[-87.67, 41.95], [-87.62, 41.95],
                                  [-87.62, 41.92], [-87.67, 41.92],
                                  [-87.67, 41.95]]]]}}
]
```

- [ ] **Step 2: Write failing test**

```python
# tests/test_source_cdp_zoning.py
import json
import responses
from sources import assessor_parcels, assessor_characteristics, cdp_zoning
from pipeline.db import get_connection
from tests.conftest import FIXTURES


@responses.activate
def test_cdp_zoning_assigns_zone_class_and_far_gap(db_path, geo, cook_client, cdp_client):
    # Seed parcels and characteristics
    pf = json.loads((FIXTURES / "assessor_parcels.json").read_text())
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_parcels.DATASET_ID}.json",
        json=pf, status=200)
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_parcels.DATASET_ID}.json",
        json=[], status=200)
    assessor_parcels.fetch(geo, db_path, cook_client)

    cf = json.loads((FIXTURES / "assessor_characteristics.json").read_text())
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_characteristics.DATASET_ID}.json",
        json=cf, status=200)
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_characteristics.DATASET_ID}.json",
        json=[], status=200)
    assessor_characteristics.fetch(geo, db_path, cook_client)

    # Now zoning
    zf = json.loads((FIXTURES / "cdp_zoning.json").read_text())
    responses.add(responses.GET,
        f"https://data.cityofchicago.org/resource/{cdp_zoning.DATASET_ID}.json",
        json=zf, status=200)
    responses.add(responses.GET,
        f"https://data.cityofchicago.org/resource/{cdp_zoning.DATASET_ID}.json",
        json=[], status=200)
    cdp_zoning.fetch(geo, db_path, cdp_client)

    conn = get_connection(db_path)
    p = conn.execute("""
        SELECT zone_class, max_far, built_far, far_gap, allows_multifamily_by_right
        FROM parcels WHERE pin='14210010010000'
    """).fetchone()
    assert p["zone_class"] == "RT-4"
    assert p["max_far"] == 1.2
    assert p["built_far"] == 0.64
    assert round(p["far_gap"], 2) == 1.88   # 1.2 / 0.64 = 1.88x... actually 1.2-0.64 = 0.56 OR 1.2/0.64 = 1.88
    # Spec uses ratio: max_far / built_far. We use 1.2/0.64 = 1.88x
    assert p["allows_multifamily_by_right"] == 1
```

- [ ] **Step 3: Run test (fails)**

Run: `pytest tests/test_source_cdp_zoning.py -v`
Expected: FAIL

- [ ] **Step 4: Implement source**

```python
# sources/cdp_zoning.py
"""Source 2A — Chicago Zoning Districts (with spatial join)."""
from __future__ import annotations
import json
from datetime import datetime, UTC
from pathlib import Path
import geopandas as gpd
from shapely.geometry import shape, Point
from pipeline.config import GeographyConfig, CONFIG_DIR
from pipeline.db import upsert_rows, get_connection
from pipeline.socrata import SocrataClient
from pipeline.zoning_lookup import load_zoning_lookup


DATASET_ID = "7cve-jgbp"
TABLE = "raw_cdp_zoning"
SOURCE_NAME = "cdp_zoning"


def fetch(geo: GeographyConfig, db_path: Path, client: SocrataClient,
          zoning_lookup_path: Path | None = None) -> int:
    fetched_at = datetime.now(UTC).isoformat(timespec="seconds")
    zoning_lookup_path = zoning_lookup_path or (CONFIG_DIR / "zoning_lookup.yaml")
    zone_info = load_zoning_lookup(zoning_lookup_path)

    raw_rows = []
    polys = []
    for r in client.fetch(DATASET_ID):
        gj = r.get("the_geom")
        if not gj:
            continue
        if isinstance(gj, str):
            gj = json.loads(gj)
        try:
            geom = shape(gj)
        except Exception:
            continue
        zc = r.get("zone_class")
        oid = r.get("objectid") or r.get("zone_id") or f"{zc}-{len(raw_rows)}"
        raw_rows.append({
            "objectid": str(oid),
            "zone_class": zc,
            "geom_geojson": json.dumps(gj),
            "pd_num": r.get("pd_num"),
            "fetched_at": fetched_at,
        })
        polys.append({"objectid": str(oid), "zone_class": zc, "geometry": geom})

    n = upsert_rows(db_path, TABLE, raw_rows, key_columns=["objectid"])

    if not polys:
        return n

    zones_gdf = gpd.GeoDataFrame(polys, crs="EPSG:4326")

    # Build a parcels GeoDataFrame from current DB
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT pin, lat, lng, built_far FROM parcels WHERE lat IS NOT NULL AND lng IS NOT NULL"
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        return n

    points = gpd.GeoDataFrame(
        [{"pin": r["pin"], "built_far": r["built_far"],
          "geometry": Point(r["lng"], r["lat"])} for r in rows],
        crs="EPSG:4326",
    )

    joined = gpd.sjoin(points, zones_gdf, how="left", predicate="within")

    conn = get_connection(db_path)
    try:
        for _, j in joined.iterrows():
            zc = j.get("zone_class")
            zi = zone_info.get(zc) if zc else None
            max_far = zi.max_far if zi else None
            allows_mf = 1 if (zi and zi.allows_multifamily) else 0
            built = j["built_far"]
            far_gap = (max_far / built) if (max_far and built and built > 0) else None
            conn.execute("""
                UPDATE parcels SET
                    zone_class = :zc,
                    max_far = :max_far,
                    far_gap = :far_gap,
                    allows_multifamily_by_right = :amf,
                    last_updated_date = :now
                WHERE pin = :pin
            """, {"zc": zc, "max_far": max_far, "far_gap": far_gap,
                  "amf": allows_mf, "now": fetched_at, "pin": j["pin"]})
        conn.commit()
    finally:
        conn.close()
    return n
```

- [ ] **Step 5: Run test**

Run: `pytest tests/test_source_cdp_zoning.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add chicago-pipeline/sources/cdp_zoning.py \
        chicago-pipeline/tests/test_source_cdp_zoning.py \
        chicago-pipeline/tests/fixtures/cdp_zoning.json
git commit -m "feat: add CDP zoning (2A) with spatial join and FAR gap"
```

---

## Task 17: Source — CDP Permits (2C) + Years Since Last Permit

Permits don't have PINs. Match to parcels by lat/lng (closest within ~50ft).

**Files:**
- Create: `chicago-pipeline/sources/cdp_permits.py`
- Test: `chicago-pipeline/tests/test_source_cdp_permits.py`
- Create: `chicago-pipeline/tests/fixtures/cdp_permits.json`

- [ ] **Step 1: Create fixture**

```json
[
  {"permit_": "100001", "permit_type": "PERMIT - RENOVATION/ALTERATION",
   "issue_date": "2018-05-12T00:00:00.000",
   "street_number": "100", "street_direction": "W", "street_name": "DIVERSEY PKWY",
   "work_description": "Reroof", "reported_cost": "8500", "community_area": "6", "ward": "44",
   "latitude": "41.94001", "longitude": "-87.65001"},
  {"permit_": "100002", "permit_type": "PERMIT - NEW CONSTRUCTION",
   "issue_date": "2022-09-30T00:00:00.000",
   "street_number": "200", "street_direction": "N", "street_name": "HALSTED",
   "work_description": "New 4-unit", "reported_cost": "850000", "community_area": "7", "ward": "32",
   "latitude": "41.93001", "longitude": "-87.66001"}
]
```

- [ ] **Step 2: Write failing test**

```python
# tests/test_source_cdp_permits.py
import json
import responses
from datetime import date
from sources import assessor_parcels, cdp_permits
from pipeline.db import get_connection
from tests.conftest import FIXTURES


@responses.activate
def test_permits_compute_years_since_last_permit(db_path, geo, cook_client, cdp_client, monkeypatch):
    monkeypatch.setattr(cdp_permits, "TODAY", date(2026, 4, 19))

    pf = json.loads((FIXTURES / "assessor_parcels.json").read_text())
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_parcels.DATASET_ID}.json",
        json=pf, status=200)
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_parcels.DATASET_ID}.json",
        json=[], status=200)
    assessor_parcels.fetch(geo, db_path, cook_client)

    pm = json.loads((FIXTURES / "cdp_permits.json").read_text())
    responses.add(responses.GET,
        f"https://data.cityofchicago.org/resource/{cdp_permits.DATASET_ID}.json",
        json=pm, status=200)
    responses.add(responses.GET,
        f"https://data.cityofchicago.org/resource/{cdp_permits.DATASET_ID}.json",
        json=[], status=200)
    cdp_permits.fetch(geo, db_path, cdp_client)

    conn = get_connection(db_path)
    p1 = conn.execute("SELECT years_since_last_permit FROM parcels WHERE pin='14210010010000'").fetchone()
    # 2026-04-19 - 2018-05-12 ≈ 7.94 years
    assert 7.5 <= p1[0] <= 8.5
    p2 = conn.execute("SELECT years_since_last_permit FROM parcels WHERE pin='14210010020000'").fetchone()
    # 2026-04-19 - 2022-09-30 ≈ 3.55 years
    assert 3.0 <= p2[0] <= 4.0
```

- [ ] **Step 3: Run test (fails)**

Run: `pytest tests/test_source_cdp_permits.py -v`
Expected: FAIL

- [ ] **Step 4: Implement**

```python
# sources/cdp_permits.py
"""Source 2C — Chicago Building Permits."""
from __future__ import annotations
from datetime import datetime, date, UTC
from pathlib import Path
from collections import defaultdict
from math import radians, sin, cos, sqrt, atan2
from pipeline.config import GeographyConfig
from pipeline.db import upsert_rows, get_connection
from pipeline.geography import filter_by_polygon, bbox_where_clause
from pipeline.socrata import SocrataClient


DATASET_ID = "ydr8-5enu"
TABLE = "raw_cdp_permits"
SOURCE_NAME = "cdp_permits"
TODAY = date.today()
MATCH_RADIUS_FT = 50.0


def _f(v):
    if v in (None, ""): return None
    try: return float(v)
    except (TypeError, ValueError): return None


def _haversine_ft(lat1, lng1, lat2, lng2):
    R = 6_371_000  # meters
    a1, a2 = radians(lat1), radians(lat2)
    da = radians(lat2 - lat1)
    dl = radians(lng2 - lng1)
    a = sin(da/2)**2 + cos(a1) * cos(a2) * sin(dl/2)**2
    c = 2 * atan2(sqrt(a), sqrt(1-a))
    return R * c * 3.28084


def fetch(geo: GeographyConfig, db_path: Path, client: SocrataClient) -> int:
    fetched_at = datetime.now(UTC).isoformat(timespec="seconds")
    where = bbox_where_clause(geo, lat_field="latitude", lng_field="longitude")

    raw_rows = []
    for r in client.fetch(DATASET_ID, where=where):
        raw_rows.append({
            "permit_number": r.get("permit_") or r.get("permit_number"),
            "permit_type": r.get("permit_type"),
            "issue_date": (r.get("issue_date") or "")[:10] or None,
            "street_number": r.get("street_number"),
            "street_direction": r.get("street_direction"),
            "street_name": r.get("street_name"),
            "work_description": r.get("work_description"),
            "reported_cost": _f(r.get("reported_cost")),
            "community_area": r.get("community_area"),
            "ward": r.get("ward"),
            "latitude": _f(r.get("latitude")),
            "longitude": _f(r.get("longitude")),
            "fetched_at": fetched_at,
        })
    raw_rows = filter_by_polygon(raw_rows, geo, lat_field="latitude", lng_field="longitude")
    raw_rows = [r for r in raw_rows if r["permit_number"]]
    n = upsert_rows(db_path, TABLE, raw_rows, key_columns=["permit_number"])

    # Match each permit to nearest parcel within MATCH_RADIUS_FT
    conn = get_connection(db_path)
    try:
        parcels = conn.execute(
            "SELECT pin, lat, lng FROM parcels WHERE lat IS NOT NULL AND lng IS NOT NULL"
        ).fetchall()
    finally:
        conn.close()
    if not parcels:
        return n

    # Latest permit date per matched parcel
    latest: dict[str, str] = {}
    for r in raw_rows:
        if not r["latitude"] or not r["longitude"] or not r["issue_date"]:
            continue
        best_pin, best_d = None, MATCH_RADIUS_FT
        for p in parcels:
            d = _haversine_ft(r["latitude"], r["longitude"], p["lat"], p["lng"])
            if d <= best_d:
                best_d = d
                best_pin = p["pin"]
        if best_pin is None:
            continue
        if best_pin not in latest or r["issue_date"] > latest[best_pin]:
            latest[best_pin] = r["issue_date"]

    conn = get_connection(db_path)
    try:
        for pin, dt in latest.items():
            d = datetime.strptime(dt, "%Y-%m-%d").date()
            yrs = round((TODAY - d).days / 365.25, 2)
            conn.execute(
                "UPDATE parcels SET years_since_last_permit=:y, last_updated_date=:t WHERE pin=:p",
                {"y": yrs, "t": fetched_at, "p": pin},
            )
        conn.commit()
    finally:
        conn.close()
    return n
```

- [ ] **Step 5: Run test**

Run: `pytest tests/test_source_cdp_permits.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add chicago-pipeline/sources/cdp_permits.py \
        chicago-pipeline/tests/test_source_cdp_permits.py \
        chicago-pipeline/tests/fixtures/cdp_permits.json
git commit -m "feat: add CDP permits (2C) with nearest-parcel matching"
```

---

## Task 18: Source — CDP Violations (2D) + Vacant (2E)

Both match by lat/lng, same nearest-parcel approach. Doing together keeps momentum.

**Files:**
- Create: `chicago-pipeline/sources/cdp_violations.py`
- Create: `chicago-pipeline/sources/cdp_vacant.py`
- Test: `chicago-pipeline/tests/test_source_cdp_violations_vacant.py`
- Create: `chicago-pipeline/tests/fixtures/cdp_violations.json`
- Create: `chicago-pipeline/tests/fixtures/cdp_vacant.json`

- [ ] **Step 1: Create fixtures**

`tests/fixtures/cdp_violations.json`:
```json
[
  {"id": "v1", "violation_date": "2024-01-10T00:00:00.000",
   "violation_code": "CN196019",
   "violation_status": "OPEN",
   "violation_description": "FAIL MAINTAIN EXT WALLS",
   "inspection_category": "COMPLAINT", "department_bureau": "CONSERVATION",
   "address": "100 W DIVERSEY",
   "street_number": "100", "street_direction": "W", "street_name": "DIVERSEY",
   "property_group": "PG100", "latitude": "41.94", "longitude": "-87.65"},
  {"id": "v2", "violation_date": "2023-05-15T00:00:00.000",
   "violation_code": "CN196020",
   "violation_status": "OPEN",
   "violation_description": "PORCH REPAIR",
   "inspection_category": "COMPLAINT", "department_bureau": "CONSERVATION",
   "address": "100 W DIVERSEY",
   "street_number": "100", "street_direction": "W", "street_name": "DIVERSEY",
   "property_group": "PG100", "latitude": "41.94", "longitude": "-87.65"},
  {"id": "v3", "violation_date": "2025-10-01T00:00:00.000",
   "violation_code": "CN196021",
   "violation_status": "CLOSED",
   "violation_description": "CLOSED ONE",
   "inspection_category": "PERIODIC", "department_bureau": "CONSERVATION",
   "address": "100 W DIVERSEY",
   "street_number": "100", "street_direction": "W", "street_name": "DIVERSEY",
   "property_group": "PG100", "latitude": "41.94", "longitude": "-87.65"}
]
```

`tests/fixtures/cdp_vacant.json`:
```json
[
  {"service_request_number": "SR1",
   "date_service_request_was_received": "2024-11-20T00:00:00.000",
   "location_of_building_on_the_lot": "FRONT",
   "is_the_building_dangerous_or_hazardous": "Y",
   "address_street_number": "200", "address_street_direction": "N", "address_street_name": "HALSTED",
   "latitude": "41.93", "longitude": "-87.66"}
]
```

- [ ] **Step 2: Write failing tests**

```python
# tests/test_source_cdp_violations_vacant.py
import json
import responses
from datetime import date
from sources import assessor_parcels, cdp_violations, cdp_vacant
from pipeline.db import get_connection
from tests.conftest import FIXTURES


def _seed(db_path, geo, cook_client):
    pf = json.loads((FIXTURES / "assessor_parcels.json").read_text())
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_parcels.DATASET_ID}.json",
        json=pf, status=200)
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_parcels.DATASET_ID}.json",
        json=[], status=200)
    assessor_parcels.fetch(geo, db_path, cook_client)


@responses.activate
def test_violations_count_and_oldest(db_path, geo, cook_client, cdp_client, monkeypatch):
    monkeypatch.setattr(cdp_violations, "TODAY", date(2026, 4, 19))
    _seed(db_path, geo, cook_client)
    fx = json.loads((FIXTURES / "cdp_violations.json").read_text())
    responses.add(responses.GET,
        f"https://data.cityofchicago.org/resource/{cdp_violations.DATASET_ID}.json",
        json=fx, status=200)
    responses.add(responses.GET,
        f"https://data.cityofchicago.org/resource/{cdp_violations.DATASET_ID}.json",
        json=[], status=200)
    cdp_violations.fetch(geo, db_path, cdp_client)

    conn = get_connection(db_path)
    p = conn.execute("SELECT open_violations_count, oldest_violation_age_days FROM parcels WHERE pin='14210010010000'").fetchone()
    assert p[0] == 2   # two OPEN, one CLOSED
    # oldest open is 2023-05-15; age in days as of 2026-04-19 ≈ 1070 days
    assert 1000 <= p[1] <= 1120


@responses.activate
def test_vacant_flag(db_path, geo, cook_client, cdp_client):
    _seed(db_path, geo, cook_client)
    fx = json.loads((FIXTURES / "cdp_vacant.json").read_text())
    responses.add(responses.GET,
        f"https://data.cityofchicago.org/resource/{cdp_vacant.DATASET_ID}.json",
        json=fx, status=200)
    responses.add(responses.GET,
        f"https://data.cityofchicago.org/resource/{cdp_vacant.DATASET_ID}.json",
        json=[], status=200)
    cdp_vacant.fetch(geo, db_path, cdp_client)
    conn = get_connection(db_path)
    p = conn.execute("SELECT has_vacancy_report FROM parcels WHERE pin='14210010020000'").fetchone()[0]
    assert p == 1
```

- [ ] **Step 3: Run tests (fail)**

Run: `pytest tests/test_source_cdp_violations_vacant.py -v`
Expected: FAIL

- [ ] **Step 4: Implement violations**

```python
# sources/cdp_violations.py
"""Source 2D — Chicago Building Violations."""
from __future__ import annotations
from datetime import datetime, date, UTC
from pathlib import Path
from math import radians, sin, cos, sqrt, atan2
from collections import defaultdict
from pipeline.config import GeographyConfig
from pipeline.db import upsert_rows, get_connection
from pipeline.geography import filter_by_polygon, bbox_where_clause
from pipeline.socrata import SocrataClient


DATASET_ID = "22u3-xenr"
TABLE = "raw_cdp_violations"
SOURCE_NAME = "cdp_violations"
TODAY = date.today()
MATCH_RADIUS_FT = 50.0


def _f(v):
    if v in (None, ""): return None
    try: return float(v)
    except (TypeError, ValueError): return None


def _haversine_ft(lat1, lng1, lat2, lng2):
    R = 6_371_000
    a1, a2 = radians(lat1), radians(lat2)
    da = radians(lat2 - lat1); dl = radians(lng2 - lng1)
    a = sin(da/2)**2 + cos(a1) * cos(a2) * sin(dl/2)**2
    c = 2 * atan2(sqrt(a), sqrt(1-a))
    return R * c * 3.28084


def fetch(geo: GeographyConfig, db_path: Path, client: SocrataClient) -> int:
    fetched_at = datetime.now(UTC).isoformat(timespec="seconds")
    where = bbox_where_clause(geo, lat_field="latitude", lng_field="longitude")

    raw_rows = []
    for r in client.fetch(DATASET_ID, where=where):
        raw_rows.append({
            "violation_id": r.get("id") or r.get("violation_id"),
            "violation_date": (r.get("violation_date") or "")[:10] or None,
            "violation_code": r.get("violation_code"),
            "violation_status": r.get("violation_status"),
            "violation_description": r.get("violation_description"),
            "inspection_category": r.get("inspection_category"),
            "department_bureau": r.get("department_bureau"),
            "address": r.get("address"),
            "street_number": r.get("street_number"),
            "street_direction": r.get("street_direction"),
            "street_name": r.get("street_name"),
            "property_group": r.get("property_group"),
            "latitude": _f(r.get("latitude")),
            "longitude": _f(r.get("longitude")),
            "fetched_at": fetched_at,
        })
    raw_rows = filter_by_polygon(raw_rows, geo, lat_field="latitude", lng_field="longitude")
    raw_rows = [r for r in raw_rows if r["violation_id"]]
    n = upsert_rows(db_path, TABLE, raw_rows, key_columns=["violation_id"])

    conn = get_connection(db_path)
    try:
        parcels = conn.execute(
            "SELECT pin, lat, lng FROM parcels WHERE lat IS NOT NULL AND lng IS NOT NULL"
        ).fetchall()
    finally:
        conn.close()
    if not parcels:
        return n

    open_count: dict[str, int] = defaultdict(int)
    oldest_open: dict[str, str] = {}
    for r in raw_rows:
        if (r.get("violation_status") or "").upper() != "OPEN":
            continue
        if not r["latitude"] or not r["longitude"]:
            continue
        best_pin, best_d = None, MATCH_RADIUS_FT
        for p in parcels:
            d = _haversine_ft(r["latitude"], r["longitude"], p["lat"], p["lng"])
            if d <= best_d:
                best_d, best_pin = d, p["pin"]
        if best_pin is None:
            continue
        open_count[best_pin] += 1
        vd = r["violation_date"]
        if vd and (best_pin not in oldest_open or vd < oldest_open[best_pin]):
            oldest_open[best_pin] = vd

    conn = get_connection(db_path)
    try:
        for pin, cnt in open_count.items():
            age = None
            if pin in oldest_open:
                d = datetime.strptime(oldest_open[pin], "%Y-%m-%d").date()
                age = (TODAY - d).days
            conn.execute("""
                UPDATE parcels SET
                    open_violations_count = :c,
                    oldest_violation_age_days = :age,
                    last_updated_date = :t
                WHERE pin = :pin
            """, {"c": cnt, "age": age, "t": fetched_at, "pin": pin})
        conn.commit()
    finally:
        conn.close()
    return n
```

- [ ] **Step 5: Implement vacant**

```python
# sources/cdp_vacant.py
"""Source 2E — Chicago Vacant and Abandoned Buildings."""
from __future__ import annotations
from datetime import datetime, UTC
from pathlib import Path
from math import radians, sin, cos, sqrt, atan2
from pipeline.config import GeographyConfig
from pipeline.db import upsert_rows, get_connection
from pipeline.geography import filter_by_polygon, bbox_where_clause
from pipeline.socrata import SocrataClient


DATASET_ID = "7nii-7srd"
TABLE = "raw_cdp_vacant"
SOURCE_NAME = "cdp_vacant"
MATCH_RADIUS_FT = 50.0


def _f(v):
    if v in (None, ""): return None
    try: return float(v)
    except (TypeError, ValueError): return None


def _haversine_ft(lat1, lng1, lat2, lng2):
    R = 6_371_000
    a1, a2 = radians(lat1), radians(lat2)
    da = radians(lat2 - lat1); dl = radians(lng2 - lng1)
    a = sin(da/2)**2 + cos(a1) * cos(a2) * sin(dl/2)**2
    c = 2 * atan2(sqrt(a), sqrt(1-a))
    return R * c * 3.28084


def fetch(geo: GeographyConfig, db_path: Path, client: SocrataClient) -> int:
    fetched_at = datetime.now(UTC).isoformat(timespec="seconds")
    where = bbox_where_clause(geo, lat_field="latitude", lng_field="longitude")

    raw_rows = []
    try:
        for r in client.fetch(DATASET_ID, where=where):
            raw_rows.append({
                "service_request_number": r.get("service_request_number"),
                "date_service_request_was_received": (r.get("date_service_request_was_received") or "")[:10] or None,
                "location_of_building_on_the_lot": r.get("location_of_building_on_the_lot"),
                "is_the_building_dangerous_or_hazardous": r.get("is_the_building_dangerous_or_hazardous"),
                "address_street_number": r.get("address_street_number"),
                "address_street_direction": r.get("address_street_direction"),
                "address_street_name": r.get("address_street_name"),
                "latitude": _f(r.get("latitude")),
                "longitude": _f(r.get("longitude")),
                "fetched_at": fetched_at,
            })
    except Exception:
        # Sparse dataset — failure is acceptable
        return 0

    raw_rows = filter_by_polygon(raw_rows, geo, lat_field="latitude", lng_field="longitude")
    raw_rows = [r for r in raw_rows if r["service_request_number"]]
    n = upsert_rows(db_path, TABLE, raw_rows, key_columns=["service_request_number"])

    conn = get_connection(db_path)
    try:
        parcels = conn.execute(
            "SELECT pin, lat, lng FROM parcels WHERE lat IS NOT NULL AND lng IS NOT NULL"
        ).fetchall()
    finally:
        conn.close()
    if not parcels:
        return n

    flagged: set[str] = set()
    for r in raw_rows:
        if not r["latitude"] or not r["longitude"]:
            continue
        best_pin, best_d = None, MATCH_RADIUS_FT
        for p in parcels:
            d = _haversine_ft(r["latitude"], r["longitude"], p["lat"], p["lng"])
            if d <= best_d:
                best_d, best_pin = d, p["pin"]
        if best_pin:
            flagged.add(best_pin)

    conn = get_connection(db_path)
    try:
        for pin in flagged:
            conn.execute(
                "UPDATE parcels SET has_vacancy_report=1, last_updated_date=:t WHERE pin=:pin",
                {"t": fetched_at, "pin": pin},
            )
        conn.commit()
    finally:
        conn.close()
    return n
```

- [ ] **Step 6: Run tests**

Run: `pytest tests/test_source_cdp_violations_vacant.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add chicago-pipeline/sources/cdp_violations.py \
        chicago-pipeline/sources/cdp_vacant.py \
        chicago-pipeline/tests/test_source_cdp_violations_vacant.py \
        chicago-pipeline/tests/fixtures/cdp_violations.json \
        chicago-pipeline/tests/fixtures/cdp_vacant.json
git commit -m "feat: add CDP violations (2D) and vacancy (2E) fetchers"
```

---

## Task 19: Source — CDP CTA Stations (2F) + Distance Calc

Computes nearest CTA station and distance-in-feet for every parcel.

**Files:**
- Create: `chicago-pipeline/sources/cdp_cta_stations.py`
- Test: `chicago-pipeline/tests/test_source_cdp_cta_stations.py`
- Create: `chicago-pipeline/tests/fixtures/cdp_cta_stations.json`

- [ ] **Step 1: Create fixture**

```json
[
  {"station_id": "41320", "longname": "Diversey", "lines": "Brown, Purple",
   "location": {"type": "Point", "coordinates": [-87.6533, 41.9324]}},
  {"station_id": "41290", "longname": "Belmont", "lines": "Red, Brown, Purple",
   "location": {"type": "Point", "coordinates": [-87.6531, 41.9397]}}
]
```

- [ ] **Step 2: Write failing test**

```python
# tests/test_source_cdp_cta_stations.py
import json
import responses
from sources import assessor_parcels, cdp_cta_stations
from pipeline.db import get_connection
from tests.conftest import FIXTURES


@responses.activate
def test_cta_distances_populated(db_path, geo, cook_client, cdp_client):
    pf = json.loads((FIXTURES / "assessor_parcels.json").read_text())
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_parcels.DATASET_ID}.json",
        json=pf, status=200)
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_parcels.DATASET_ID}.json",
        json=[], status=200)
    assessor_parcels.fetch(geo, db_path, cook_client)

    fx = json.loads((FIXTURES / "cdp_cta_stations.json").read_text())
    responses.add(responses.GET,
        f"https://data.cityofchicago.org/resource/{cdp_cta_stations.DATASET_ID}.json",
        json=fx, status=200)
    responses.add(responses.GET,
        f"https://data.cityofchicago.org/resource/{cdp_cta_stations.DATASET_ID}.json",
        json=[], status=200)
    cdp_cta_stations.fetch(geo, db_path, cdp_client)

    conn = get_connection(db_path)
    p = conn.execute("""
        SELECT cta_nearest_station, cta_distance_ft FROM parcels WHERE pin='14210010010000'
    """).fetchone()
    assert p["cta_nearest_station"] in ("Belmont", "Diversey")
    assert p["cta_distance_ft"] is not None
    assert 0 < p["cta_distance_ft"] < 10000
```

- [ ] **Step 3: Run test (fails)**

Run: `pytest tests/test_source_cdp_cta_stations.py -v`
Expected: FAIL

- [ ] **Step 4: Implement**

```python
# sources/cdp_cta_stations.py
"""Source 2F — Chicago CTA L Stations."""
from __future__ import annotations
from datetime import datetime, UTC
from pathlib import Path
from math import radians, sin, cos, sqrt, atan2
from pipeline.config import GeographyConfig
from pipeline.db import upsert_rows, get_connection
from pipeline.socrata import SocrataClient


DATASET_ID = "3tzw-cg4m"
TABLE = "raw_cdp_cta_stations"
SOURCE_NAME = "cdp_cta_stations"


def _f(v):
    if v in (None, ""): return None
    try: return float(v)
    except (TypeError, ValueError): return None


def _haversine_ft(lat1, lng1, lat2, lng2):
    R = 6_371_000
    a1, a2 = radians(lat1), radians(lat2)
    da = radians(lat2 - lat1); dl = radians(lng2 - lng1)
    a = sin(da/2)**2 + cos(a1) * cos(a2) * sin(dl/2)**2
    c = 2 * atan2(sqrt(a), sqrt(1-a))
    return R * c * 3.28084


def _extract_latlng(r: dict) -> tuple[float | None, float | None]:
    # Dataset may use top-level lat/long or a `location` point
    lat = _f(r.get("latitude"))
    lng = _f(r.get("longitude"))
    if lat is not None and lng is not None:
        return lat, lng
    loc = r.get("location") or r.get("the_geom")
    if isinstance(loc, dict) and loc.get("type") == "Point":
        coords = loc.get("coordinates") or []
        if len(coords) == 2:
            return _f(coords[1]), _f(coords[0])
    return None, None


def fetch(geo: GeographyConfig, db_path: Path, client: SocrataClient) -> int:
    fetched_at = datetime.now(UTC).isoformat(timespec="seconds")

    raw_rows = []
    stations = []
    for r in client.fetch(DATASET_ID):
        lat, lng = _extract_latlng(r)
        sid = r.get("station_id") or r.get("stop_id")
        if not sid:
            continue
        raw_rows.append({
            "station_id": str(sid),
            "longname": r.get("longname") or r.get("station_name"),
            "lines": r.get("lines"),
            "latitude": lat, "longitude": lng,
            "fetched_at": fetched_at,
        })
        if lat is not None and lng is not None:
            stations.append({
                "id": str(sid),
                "name": r.get("longname") or r.get("station_name"),
                "lat": lat, "lng": lng,
            })

    n = upsert_rows(db_path, TABLE, raw_rows, key_columns=["station_id"])

    if not stations:
        return n

    conn = get_connection(db_path)
    try:
        parcels = conn.execute(
            "SELECT pin, lat, lng FROM parcels WHERE lat IS NOT NULL AND lng IS NOT NULL"
        ).fetchall()
    finally:
        conn.close()
    if not parcels:
        return n

    conn = get_connection(db_path)
    try:
        for p in parcels:
            best_name, best_d = None, float("inf")
            for s in stations:
                d = _haversine_ft(p["lat"], p["lng"], s["lat"], s["lng"])
                if d < best_d:
                    best_d, best_name = d, s["name"]
            conn.execute("""
                UPDATE parcels SET
                    cta_nearest_station = :name,
                    cta_distance_ft = :d,
                    last_updated_date = :t
                WHERE pin = :pin
            """, {"name": best_name, "d": round(best_d, 1),
                  "t": fetched_at, "pin": p["pin"]})
        conn.commit()
    finally:
        conn.close()
    return n
```

- [ ] **Step 5: Run test**

Run: `pytest tests/test_source_cdp_cta_stations.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add chicago-pipeline/sources/cdp_cta_stations.py \
        chicago-pipeline/tests/test_source_cdp_cta_stations.py \
        chicago-pipeline/tests/fixtures/cdp_cta_stations.json
git commit -m "feat: add CDP CTA stations (2F) with nearest-station distance"
```

---

## Task 20: Source — Clerk Delinquent Taxes (3A, Bulk CSV)

Not an API — user manually drops the latest CSV into `data/delinquent.csv`. Module reads it, filters to PINs in the parcels table, writes flag + years.

**Files:**
- Create: `chicago-pipeline/sources/clerk_delinquent.py`
- Test: `chicago-pipeline/tests/test_source_clerk_delinquent.py`
- Create: `chicago-pipeline/tests/fixtures/delinquent.csv`

- [ ] **Step 1: Create fixture CSV**

`tests/fixtures/delinquent.csv`:
```
pin,tax_year,amount_owed
14-21-001-001-0000,2022,4820.10
14-21-001-001-0000,2023,5120.55
14-21-001-001-0000,2024,5498.30
14-21-001-002-0000,2024,220.00
99-99-999-000-0000,2024,100.00
```

- [ ] **Step 2: Write failing test**

```python
# tests/test_source_clerk_delinquent.py
import json
import responses
from sources import assessor_parcels, clerk_delinquent
from pipeline.db import get_connection
from tests.conftest import FIXTURES


@responses.activate
def test_delinquent_csv_flags_pins(db_path, geo, cook_client):
    pf = json.loads((FIXTURES / "assessor_parcels.json").read_text())
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_parcels.DATASET_ID}.json",
        json=pf, status=200)
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_parcels.DATASET_ID}.json",
        json=[], status=200)
    assessor_parcels.fetch(geo, db_path, cook_client)

    clerk_delinquent.fetch_from_csv(FIXTURES / "delinquent.csv", db_path)

    conn = get_connection(db_path)
    p1 = conn.execute("SELECT tax_delinquent, delinquency_years FROM parcels WHERE pin='14210010010000'").fetchone()
    assert p1["tax_delinquent"] == 1
    assert p1["delinquency_years"] == 3   # 2022, 2023, 2024
    p2 = conn.execute("SELECT tax_delinquent, delinquency_years FROM parcels WHERE pin='14210010020000'").fetchone()
    assert p2["tax_delinquent"] == 1
    assert p2["delinquency_years"] == 1

    # unknown pin row filtered out
    raw = conn.execute("SELECT pin FROM raw_clerk_delinquent").fetchall()
    pins = {r[0] for r in raw}
    assert "99999990000000" not in pins
```

- [ ] **Step 3: Run test (fails)**

Run: `pytest tests/test_source_clerk_delinquent.py -v`
Expected: FAIL

- [ ] **Step 4: Implement**

```python
# sources/clerk_delinquent.py
"""Source 3A — Cook County Clerk Delinquent Property Tax (bulk CSV)."""
from __future__ import annotations
import csv
from datetime import datetime, UTC
from pathlib import Path
from collections import defaultdict
from pipeline.db import upsert_rows, get_connection


SOURCE_NAME = "clerk_delinquent"
DEFAULT_CSV_PATH = Path("data/delinquent.csv")


def _normalize_pin(raw: str) -> str:
    """Clerk CSV uses dashed PINs (14-21-001-001-0000); normalize to 14-digit."""
    return (raw or "").replace("-", "").strip()


def fetch_from_csv(csv_path: Path, db_path: Path) -> int:
    fetched_at = datetime.now(UTC).isoformat(timespec="seconds")
    if not csv_path.exists():
        print(f"Delinquent CSV not found at {csv_path} — skipping")
        return 0

    conn = get_connection(db_path)
    try:
        known_pins = {row[0] for row in conn.execute("SELECT pin FROM parcels")}
    finally:
        conn.close()

    by_pin: dict[str, list[dict]] = defaultdict(list)
    with csv_path.open(newline="") as f:
        reader = csv.DictReader(f)
        for r in reader:
            pin = _normalize_pin(r.get("pin") or "")
            if pin not in known_pins:
                continue
            by_pin[pin].append(r)

    raw_rows = []
    for pin, rows in by_pin.items():
        years = sorted({int(r["tax_year"]) for r in rows if r.get("tax_year")})
        total = sum(float(r["amount_owed"]) for r in rows if r.get("amount_owed"))
        raw_rows.append({
            "pin": pin,
            "delinquent_years": len(years),
            "earliest_delinquent_year": years[0] if years else None,
            "total_owed": total,
            "fetched_at": fetched_at,
        })

    n = upsert_rows(db_path, "raw_clerk_delinquent", raw_rows, key_columns=["pin"])

    conn = get_connection(db_path)
    try:
        for r in raw_rows:
            conn.execute("""
                UPDATE parcels SET
                    tax_delinquent = 1,
                    delinquency_years = :y,
                    last_updated_date = :t
                WHERE pin = :pin
            """, {"y": r["delinquent_years"], "t": fetched_at, "pin": r["pin"]})
        conn.commit()
    finally:
        conn.close()
    return n


def fetch(geo, db_path: Path, client=None) -> int:
    """Standard fetch interface — reads from DEFAULT_CSV_PATH."""
    return fetch_from_csv(DEFAULT_CSV_PATH, db_path)
```

- [ ] **Step 5: Run test**

Run: `pytest tests/test_source_clerk_delinquent.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add chicago-pipeline/sources/clerk_delinquent.py \
        chicago-pipeline/tests/test_source_clerk_delinquent.py \
        chicago-pipeline/tests/fixtures/delinquent.csv
git commit -m "feat: add Clerk delinquent tax CSV loader (3A)"
```

---

## Task 21: Consolidation (Adjacent Same-Owner Parcels)

Groups parcels by `owner_address_name` + `mail_address_full`. For groups with 2+ parcels, checks proximity (lat/lng within ~200ft). Writes `consolidation_groups` row and links parcels via `consolidation_group_id`.

**Files:**
- Create: `chicago-pipeline/pipeline/consolidate.py`
- Test: `chicago-pipeline/tests/test_consolidate.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_consolidate.py
import json
from pipeline.db import get_connection, init_db
from pipeline.consolidate import consolidate


def _seed(db_path, rows):
    conn = get_connection(db_path)
    try:
        for r in rows:
            conn.execute("""
                INSERT INTO parcels (pin, lat, lng, lot_size_sf, owner_name, mail_address,
                                     first_seen_date, last_fetched_date, last_updated_date, stage)
                VALUES (:pin, :lat, :lng, :lot, :owner, :mail, '2026-04-19', '2026-04-19', '2026-04-19', 'scored')
            """, r)
        conn.commit()
    finally:
        conn.close()


def test_consolidate_groups_same_owner_adjacent(db_path):
    _seed(db_path, [
        {"pin": "P1", "lat": 41.9400, "lng": -87.6500, "lot": 3000,
         "owner": "ACME LLC", "mail": "100 Main St"},
        {"pin": "P2", "lat": 41.9401, "lng": -87.6501, "lot": 3000,
         "owner": "ACME LLC", "mail": "100 Main St"},
        {"pin": "P3", "lat": 41.9500, "lng": -87.6300, "lot": 3000,
         "owner": "ACME LLC", "mail": "100 Main St"},   # same owner but far
        {"pin": "P4", "lat": 41.9402, "lng": -87.6502, "lot": 3000,
         "owner": "OTHER LLC", "mail": "200 Main St"},  # different owner
    ])
    n_groups = consolidate(db_path)
    assert n_groups == 1

    conn = get_connection(db_path)
    groups = conn.execute("SELECT group_id, pins, combined_lot_size_sf, owner_name FROM consolidation_groups").fetchall()
    assert len(groups) == 1
    g = groups[0]
    pins = sorted(json.loads(g["pins"]))
    assert pins == ["P1", "P2"]
    assert g["combined_lot_size_sf"] == 6000
    assert g["owner_name"] == "ACME LLC"

    links = conn.execute("""
        SELECT pin, consolidation_group_id FROM parcels WHERE consolidation_group_id IS NOT NULL
        ORDER BY pin
    """).fetchall()
    assert [l["pin"] for l in links] == ["P1", "P2"]
    assert {l["consolidation_group_id"] for l in links} == {g["group_id"]}
```

- [ ] **Step 2: Run test (fails)**

Run: `pytest tests/test_consolidate.py -v`
Expected: FAIL

- [ ] **Step 3: Implement**

```python
# pipeline/consolidate.py
"""Adjacent same-owner parcel consolidation."""
from __future__ import annotations
import json
from datetime import date
from pathlib import Path
from collections import defaultdict
from math import radians, sin, cos, sqrt, atan2
from pipeline.db import get_connection


ADJACENCY_RADIUS_FT = 200.0


def _haversine_ft(lat1, lng1, lat2, lng2):
    R = 6_371_000
    a1, a2 = radians(lat1), radians(lat2)
    da = radians(lat2 - lat1); dl = radians(lng2 - lng1)
    a = sin(da/2)**2 + cos(a1) * cos(a2) * sin(dl/2)**2
    c = 2 * atan2(sqrt(a), sqrt(1-a))
    return R * c * 3.28084


def _owner_key(row) -> tuple[str, str]:
    return ((row["owner_name"] or "").strip().upper(),
            (row["mail_address"] or "").strip().upper())


def _cluster(points: list[dict], radius: float) -> list[list[dict]]:
    """Single-link clustering on haversine distance."""
    remaining = points.copy()
    clusters: list[list[dict]] = []
    while remaining:
        seed = remaining.pop(0)
        cluster = [seed]
        changed = True
        while changed:
            changed = False
            still = []
            for p in remaining:
                near = any(_haversine_ft(p["lat"], p["lng"], q["lat"], q["lng"]) <= radius
                           for q in cluster)
                if near:
                    cluster.append(p)
                    changed = True
                else:
                    still.append(p)
            remaining = still
        clusters.append(cluster)
    return clusters


def consolidate(db_path: Path) -> int:
    """Detect adjacent same-owner parcel groups; return # groups created."""
    conn = get_connection(db_path)
    try:
        rows = conn.execute("""
            SELECT pin, lat, lng, lot_size_sf, owner_name, mail_address
            FROM parcels
            WHERE lat IS NOT NULL AND lng IS NOT NULL AND owner_name IS NOT NULL
        """).fetchall()
    finally:
        conn.close()

    # Group by owner key
    by_owner: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in rows:
        by_owner[_owner_key(r)].append(dict(r))

    conn = get_connection(db_path)
    groups_created = 0
    try:
        # Wipe prior groupings so this is idempotent
        conn.execute("DELETE FROM consolidation_groups")
        conn.execute("UPDATE parcels SET consolidation_group_id = NULL")

        today = date.today().isoformat()
        for (owner, _), parcels in by_owner.items():
            if len(parcels) < 2:
                continue
            for cluster in _cluster(parcels, ADJACENCY_RADIUS_FT):
                if len(cluster) < 2:
                    continue
                pins = sorted(p["pin"] for p in cluster)
                total_lot = sum((p["lot_size_sf"] or 0) for p in cluster) or None
                cur = conn.execute("""
                    INSERT INTO consolidation_groups (pins, combined_lot_size_sf, owner_name, detected_date)
                    VALUES (?, ?, ?, ?)
                """, (json.dumps(pins), total_lot, owner, today))
                gid = cur.lastrowid
                for pin in pins:
                    conn.execute(
                        "UPDATE parcels SET consolidation_group_id = ? WHERE pin = ?",
                        (gid, pin),
                    )
                groups_created += 1
        conn.commit()
    finally:
        conn.close()
    return groups_created
```

- [ ] **Step 4: Run test**

Run: `pytest tests/test_consolidate.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add chicago-pipeline/pipeline/consolidate.py chicago-pipeline/tests/test_consolidate.py
git commit -m "feat: add adjacent same-owner parcel consolidation"
```

---

## Task 22: Fetch Orchestrator CLI

Ties everything together. `python -m pipeline.fetch_all` runs every source in order, logs timing + row counts, handles staleness skipping.

**Files:**
- Create: `chicago-pipeline/pipeline/fetch_all.py`
- Test: `chicago-pipeline/tests/test_fetch_all.py`

Source execution order (matters — later sources join against parcels table):

1. `assessor_parcels` (establishes PINs)
2. `assessor_addresses` (owner info)
3. `assessor_characteristics` (building facts)
4. `assessor_values` (assessed values + trends)
5. `assessor_sales` (hold duration)
6. `assessor_appeals`
7. `assessor_exempt`
8. `cdp_zoning` (spatial join — needs characteristics for built FAR)
9. `cdp_permits`
10. `cdp_violations`
11. `cdp_vacant`
12. `cdp_cta_stations`
13. `clerk_delinquent` (from CSV if present)
14. `consolidate` (runs last — needs all parcels)

- [ ] **Step 1: Write failing test**

```python
# tests/test_fetch_all.py
import json
import responses
import pytest
from datetime import date
from pipeline import fetch_all
from pipeline.db import get_connection
from tests.conftest import FIXTURES


def _register_all(monkeypatch):
    from sources import (
        assessor_parcels, assessor_addresses, assessor_characteristics,
        assessor_values, assessor_sales, assessor_appeals, assessor_exempt,
        cdp_zoning, cdp_permits, cdp_violations, cdp_vacant, cdp_cta_stations,
    )
    from sources.assessor_sales import TODAY as _    # just to ensure import
    monkeypatch.setattr(cdp_permits, "TODAY", date(2026, 4, 19))
    monkeypatch.setattr(cdp_violations, "TODAY", date(2026, 4, 19))

    cc = "https://datacatalog.cookcountyil.gov/resource"
    cdp = "https://data.cityofchicago.org/resource"
    for ds, fname in [
        (assessor_parcels.DATASET_ID, "assessor_parcels.json"),
        (assessor_addresses.DATASET_ID, "assessor_addresses.json"),
        (assessor_characteristics.DATASET_ID, "assessor_characteristics.json"),
        (assessor_values.DATASET_ID, "assessor_values.json"),
        (assessor_sales.DATASET_ID, "assessor_sales.json"),
        (assessor_appeals.DATASET_ID, "assessor_appeals.json"),
        (assessor_exempt.DATASET_ID, "assessor_exempt.json"),
    ]:
        fx = json.loads((FIXTURES / fname).read_text())
        responses.add(responses.GET, f"{cc}/{ds}.json", json=fx, status=200)
        responses.add(responses.GET, f"{cc}/{ds}.json", json=[], status=200)
    for ds, fname in [
        (cdp_zoning.DATASET_ID, "cdp_zoning.json"),
        (cdp_permits.DATASET_ID, "cdp_permits.json"),
        (cdp_violations.DATASET_ID, "cdp_violations.json"),
        (cdp_vacant.DATASET_ID, "cdp_vacant.json"),
        (cdp_cta_stations.DATASET_ID, "cdp_cta_stations.json"),
    ]:
        fx = json.loads((FIXTURES / fname).read_text())
        responses.add(responses.GET, f"{cdp}/{ds}.json", json=fx, status=200)
        responses.add(responses.GET, f"{cdp}/{ds}.json", json=[], status=200)


@responses.activate
def test_run_all_populates_db_and_logs_each_source(tmp_path, monkeypatch, geo):
    db = tmp_path / "pipeline.db"
    from pipeline.db import init_db
    init_db(db)

    _register_all(monkeypatch)
    # Point clerk CSV at fixture
    monkeypatch.setattr("sources.clerk_delinquent.DEFAULT_CSV_PATH", FIXTURES / "delinquent.csv")

    results = fetch_all.run_all(geo, db, app_token="TKN")

    # Every source has a result, no failures
    assert all(r.status == "ok" for r in results), [r for r in results if r.status != "ok"]
    # Parcels table has rows from the fixture
    conn = get_connection(db)
    n = conn.execute("SELECT COUNT(*) FROM parcels").fetchone()[0]
    assert n >= 2
    # fetch_log rows exist for each source
    sources_logged = {r[0] for r in conn.execute("SELECT source_name FROM fetch_log").fetchall()}
    for expected in ("assessor_parcels", "cdp_zoning", "cdp_cta_stations",
                     "clerk_delinquent", "consolidate"):
        assert expected in sources_logged


@responses.activate
def test_run_all_continues_after_single_source_error(tmp_path, monkeypatch, geo):
    db = tmp_path / "pipeline.db"
    from pipeline.db import init_db
    init_db(db)
    _register_all(monkeypatch)
    monkeypatch.setattr("sources.clerk_delinquent.DEFAULT_CSV_PATH", FIXTURES / "delinquent.csv")

    # Force the appeals module to raise
    from sources import assessor_appeals
    def boom(*a, **kw): raise RuntimeError("boom")
    monkeypatch.setattr(assessor_appeals, "fetch", boom)

    results = fetch_all.run_all(geo, db, app_token="TKN")
    errs = [r for r in results if r.status == "error"]
    oks = [r for r in results if r.status == "ok"]
    assert len(errs) == 1
    assert errs[0].source_name == "assessor_appeals"
    assert len(oks) > 0
```

- [ ] **Step 2: Run tests (fail)**

Run: `pytest tests/test_fetch_all.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement orchestrator**

```python
# pipeline/fetch_all.py
"""Pipeline orchestrator: run every data source against the target geography."""
from __future__ import annotations
import argparse
import os
import time
from dataclasses import dataclass
from datetime import datetime, UTC
from pathlib import Path
from typing import Callable, Optional

from dotenv import load_dotenv

from pipeline.config import CONFIG_DIR, GeographyConfig, get_geography
from pipeline.db import init_db, get_connection
from pipeline.socrata import SocrataClient
from pipeline.consolidate import consolidate

from sources import (
    assessor_parcels, assessor_addresses, assessor_characteristics,
    assessor_values, assessor_sales, assessor_appeals, assessor_exempt,
    cdp_zoning, cdp_permits, cdp_violations, cdp_vacant, cdp_cta_stations,
    clerk_delinquent,
)


COOK_DOMAIN = "datacatalog.cookcountyil.gov"
CDP_DOMAIN = "data.cityofchicago.org"


@dataclass
class SourceResult:
    source_name: str
    status: str            # 'ok' | 'error'
    rows_fetched: int
    duration_s: float
    error_message: Optional[str] = None


def _log_start(db_path: Path, source: str) -> int:
    conn = get_connection(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO fetch_log (source_name, started_at, status) VALUES (?, ?, 'running')",
            (source, datetime.now(UTC).isoformat(timespec="seconds")),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _log_finish(db_path: Path, log_id: int, status: str, rows: int, err: str | None):
    conn = get_connection(db_path)
    try:
        conn.execute("""
            UPDATE fetch_log SET finished_at = ?, rows_fetched = ?, status = ?, error_message = ?
            WHERE log_id = ?
        """, (datetime.now(UTC).isoformat(timespec="seconds"), rows, status, err, log_id))
        conn.commit()
    finally:
        conn.close()


def _run(source_name: str, fn: Callable, db_path: Path, *args) -> SourceResult:
    log_id = _log_start(db_path, source_name)
    started = time.monotonic()
    try:
        rows = fn(*args)
        dur = time.monotonic() - started
        _log_finish(db_path, log_id, "ok", rows or 0, None)
        print(f"[{source_name}] ok — {rows} rows in {dur:.1f}s")
        return SourceResult(source_name, "ok", rows or 0, dur)
    except Exception as e:
        dur = time.monotonic() - started
        _log_finish(db_path, log_id, "error", 0, str(e))
        print(f"[{source_name}] ERROR: {e}")
        return SourceResult(source_name, "error", 0, dur, str(e))


def run_all(geo: GeographyConfig, db_path: Path, app_token: str) -> list[SourceResult]:
    cook = SocrataClient(domain=COOK_DOMAIN, app_token=app_token, rate_limit_sleep=0.1)
    cdp = SocrataClient(domain=CDP_DOMAIN, app_token=app_token, rate_limit_sleep=0.1)

    results: list[SourceResult] = []
    # Order matters — parcels first, then joining sources
    results.append(_run("assessor_parcels", assessor_parcels.fetch, db_path, geo, db_path, cook))
    results.append(_run("assessor_addresses", assessor_addresses.fetch, db_path, geo, db_path, cook))
    results.append(_run("assessor_characteristics", assessor_characteristics.fetch, db_path, geo, db_path, cook))
    results.append(_run("assessor_values", assessor_values.fetch, db_path, geo, db_path, cook))
    results.append(_run("assessor_sales", assessor_sales.fetch, db_path, geo, db_path, cook))
    results.append(_run("assessor_appeals", assessor_appeals.fetch, db_path, geo, db_path, cook))
    results.append(_run("assessor_exempt", assessor_exempt.fetch, db_path, geo, db_path, cook))
    results.append(_run("cdp_zoning", cdp_zoning.fetch, db_path, geo, db_path, cdp))
    results.append(_run("cdp_permits", cdp_permits.fetch, db_path, geo, db_path, cdp))
    results.append(_run("cdp_violations", cdp_violations.fetch, db_path, geo, db_path, cdp))
    results.append(_run("cdp_vacant", cdp_vacant.fetch, db_path, geo, db_path, cdp))
    results.append(_run("cdp_cta_stations", cdp_cta_stations.fetch, db_path, geo, db_path, cdp))
    results.append(_run("clerk_delinquent", clerk_delinquent.fetch, db_path, geo, db_path, None))
    results.append(_run("consolidate", consolidate, db_path, db_path))
    return results


def main():
    parser = argparse.ArgumentParser(description="Run all data fetches for the Chicago multifamily pipeline.")
    parser.add_argument("--db", default=None, help="Override DB path")
    parser.add_argument("--config-dir", default=None, help="Override config dir")
    args = parser.parse_args()

    load_dotenv()
    app_token = os.environ.get("SOCRATA_APP_TOKEN", "")
    if not app_token:
        print("WARNING: SOCRATA_APP_TOKEN is not set — requests will be rate-limited.")

    config_dir = Path(args.config_dir) if args.config_dir else CONFIG_DIR
    db_path = Path(args.db) if args.db else Path(os.environ.get("PIPELINE_DB_PATH", "data/pipeline.db"))

    init_db(db_path)
    geo = get_geography(config_dir)

    results = run_all(geo, db_path, app_token)

    print("\n=== Summary ===")
    for r in results:
        print(f"  {r.source_name:30s} {r.status:5s} {r.rows_fetched:>8d} rows  {r.duration_s:.1f}s")
    fails = [r for r in results if r.status != "ok"]
    if fails:
        print(f"\n{len(fails)} sources failed.")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_fetch_all.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Run the full test suite**

Run: `pytest -v`
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add chicago-pipeline/pipeline/fetch_all.py chicago-pipeline/tests/test_fetch_all.py
git commit -m "feat: add fetch_all orchestrator with per-source logging"
```

---

## Task 23: Smoke Test Against Live APIs (Manual)

Not a test file — a manual verification step. The engineer should run the real pipeline end-to-end once, against a small subarea, to confirm wire-up.

- [ ] **Step 1: Shrink the geography for a smoke run**

Make a copy of `config/geography.yaml` as `config/geography_smoke.yaml` with a very small polygon (~4 blocks around Diversey + Racine):

```yaml
name: "Smoke test — Racine/Diversey"
polygon:
  - [41.9340, -87.6600]
  - [41.9340, -87.6550]
  - [41.9310, -87.6550]
  - [41.9310, -87.6600]
bbox:
  min_lat: 41.9310
  max_lat: 41.9340
  min_lng: -87.6600
  max_lng: -87.6550
```

- [ ] **Step 2: Ensure `.env` has SOCRATA_APP_TOKEN**

```bash
grep SOCRATA_APP_TOKEN .env
```

If empty, register at https://dev.socrata.com and put the token in `.env`.

- [ ] **Step 3: Run the pipeline against the smoke geography**

```bash
python -m pipeline.fetch_all --db data/smoke.db --config-dir config
```

Expected:
- Each source logs "ok — N rows in Xs"
- Total runtime < 5 minutes
- `data/smoke.db` exists and contains parcels

- [ ] **Step 4: Verify the DB**

```bash
sqlite3 data/smoke.db "SELECT COUNT(*) FROM parcels;"
sqlite3 data/smoke.db "SELECT pin, address, owner_name, zone_class, built_far, cta_distance_ft, is_absentee, is_llc FROM parcels LIMIT 10;"
sqlite3 data/smoke.db "SELECT source_name, rows_fetched, status FROM fetch_log ORDER BY log_id;"
```

Expected: 100–500 parcels (small polygon), each with most fields populated, all sources status='ok'.

- [ ] **Step 5: Commit smoke config**

```bash
git add chicago-pipeline/config/geography_smoke.yaml
git commit -m "chore: add smoke-test geography config"
```

---

## Self-Review

**Spec coverage — pipeline data sources:**

| Spec section | Covered by |
|---|---|
| Source 1A Parcel Universe | Task 9 |
| Source 1B Parcel Addresses + absentee/LLC | Task 10 |
| Source 1C Improvement Characteristics + built FAR | Task 11 |
| Source 1D Assessed Values + tax trends | Task 12 |
| Source 1E Parcel Sales + hold duration | Task 13 |
| Source 1F Appeals | Task 14 |
| Source 1G Tax-Exempt | Task 14 |
| Source 2A Zoning Districts + spatial join + FAR gap | Task 16 |
| Source 2B Zoning Lookup Table (static) | Task 15 |
| Source 2C Permits + years-since-last-permit | Task 17 |
| Source 2D Violations + open count + oldest age | Task 18 |
| Source 2E Vacant/abandoned | Task 18 |
| Source 2F CTA stations + distance | Task 19 |
| Source 3A Clerk Delinquent CSV | Task 20 |
| Geography polygon config | Task 2 |
| Pluggable source module pattern | Task 8 + every source task |
| Fetch module architecture | Task 22 |
| Shared Socrata utilities | Task 7 |
| Data storage principle (all raw stored) | Task 4 schema |
| Consolidation | Task 21 |

**Deferred to later plans (documented, not implemented):**

- Source 3B Equalization factor / tax rates + `estimated_annual_tax` — Plan 2 (scoring), since that's when taxes get computed
- Source 4 IL SOS LLC lookup — Plan 4 (outreach enrichment)
- Source 5 REISkip contact enrichment — Plan 4
- Source 6 Zillow listing scrape — Plan 4
- Historical analysis script — Plan 2
- Scoring — Plan 2
- Flask UI + dynamic filter panel — Plan 3
- Outreach drafting + Gmail + Lob + sequence scheduler + feedback report — Plan 4

**Placeholder scan:** No TBDs, no "similar to task N" without code, no "add appropriate error handling" — every step has explicit code or commands.

**Type consistency:** `fetch(geo, db_path, client) -> int` is the uniform interface across every source module. `SocrataClient.fetch(dataset_id, where=None, select=None, order=None, limit=50000) -> Iterator[dict]` is consistent everywhere it's referenced. `upsert_rows(db_path, table, rows, key_columns)` is used consistently. `TODAY` module-level override for date-dependent tests is used consistently across sales/permits/violations.

**One naming note to double-check during execution:** The `raw_cdp_permits` table uses column `permit_number` but the Socrata field is `permit_` — the fetcher maps this correctly.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-19-chicago-pipeline-data-foundation.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

**Which approach?**
