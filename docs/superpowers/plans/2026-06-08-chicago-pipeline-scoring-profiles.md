# Scoring Profiles: ADU + Redevelopment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two new scoring profiles (`adu`, `redev`) that surface ADU candidates and redevelopment opportunities, ranked by their own `score_adu` / `score_redev` columns and selectable via a new UI dropdown that also auto-applies recommended filters per profile.

**Architecture:** Multi-profile scoring engine — one pass writes one `score_<profile>` column per registered YAML. Existing `config/scoring.yaml` continues to write the legacy `score` column (the value-add profile, unchanged). New `config/profile_defaults.yaml` is the registry binding profile name → YAML path → score column → recommended UI filters. No filter logic in scoring YAML; filters stay in the UI layer via `webapp/filter_schema.py`. Two new data columns (`lot_width_ft`, `adu_eligible` + supporting columns) are derived from data we already fetch or from a new ArcGIS source.

**Tech Stack:** Python 3.14 + SQLite (WAL), Flask webapp, vanilla JS frontend, pytest, geopandas + shapely for GIS, Socrata + Esri ArcGIS REST APIs for data fetches.

**Spec:** [`docs/superpowers/specs/2026-06-08-scoring-adu-redev-design.md`](../specs/2026-06-08-scoring-adu-redev-design.md)

**Per the spec's standing instruction:** Phases 1–3 ship without activating any new scoring weights. Phase 4 introduces the YAMLs and is gated on Hunter explicitly approving the proposed signal weights before merge.

---

## File map

**New files:**
- `sources/chicago_adu_zones.py` — ArcGIS polygon fetcher + spatial join (Phase 3)
- `tests/test_source_chicago_adu_zones.py` (Phase 3)
- `tests/fixtures/chicago_adu_zones.json` (Phase 3)
- `config/scoring_adu.yaml` (Phase 4)
- `config/scoring_redev.yaml` (Phase 4)
- `config/profile_defaults.yaml` (Phase 4)
- `tests/test_profile_defaults_loader.py` (Phase 4)

**Modified files:**
- `pipeline/db.py` — add 8 columns to `_LATER_COLUMNS["parcels"]`; add `width_ft`/`depth_ft` to `raw_ccgis_parcels` schema; new `raw_chicago_adu_zones` table (Phase 1 + 3)
- `sources/ccgis_parcels.py` — extract `width_ft`/`depth_ft` from `minimum_rotated_rectangle`; persist + write to `parcels` (Phase 2)
- `tests/test_source_ccgis_parcels.py` — add lot width/depth tests (Phase 2)
- `pipeline/score.py` — derive `last_sale_price_recent` as a pre-step; refactor `score_parcels` to multi-profile output (Phase 4)
- `tests/test_score.py` — multi-profile + derived-column tests (Phase 4)
- `pipeline/fetch_all.py` — register `chicago_adu_zones` source (Phase 3); call multi-profile scoring (Phase 4)
- `webapp/filter_schema.py` — config-yaml-driven, no code change; covered by `config/ui_filters.yaml` additions (Phase 4)
- `config/ui_filters.yaml` — add filterable column entries (Phase 4)
- `webapp/routes.py` — new `/api/profile-defaults` route; `/api/parcels` accepts `?profile=` (Phase 5)
- `webapp/templates/index.html` — profile dropdown markup (Phase 5)
- `webapp/static/js/app.js` (or wherever the parcels list controller lives — see Task 5.3) — dropdown change handler (Phase 5)
- `webapp/static/css/style.css` — minor styling for the dropdown (Phase 5)

---

# Phase 1: Schema migration

Add 8 new columns to `parcels` via the `_LATER_COLUMNS` mechanism. Pure additive; no data writes yet.

## Task 1.1: Add 8 columns to `_LATER_COLUMNS["parcels"]`

**Files:**
- Modify: `pipeline/db.py:482-505`
- Test: `tests/test_db.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_db.py`:

```python
def test_init_db_adds_scoring_profile_columns(tmp_path):
    """Phase 1 of the scoring-profiles plan adds 8 columns to parcels:
    2 lot-geometry, 3 ADU-eligibility, 1 derived sale-price, 2 score
    columns. init_db must be idempotent on a fresh and a pre-existing DB."""
    from pipeline.db import init_db
    import sqlite3
    p = tmp_path / "t.db"
    init_db(p)
    # Re-init on existing DB must not raise.
    init_db(p)

    conn = sqlite3.connect(p)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(parcels)")}
    expected = {
        "lot_width_ft", "lot_depth_ft",
        "adu_eligible", "adu_restriction_text", "adu_has_annual_limits",
        "last_sale_price_recent",
        "score_adu", "score_redev",
    }
    missing = expected - cols
    assert not missing, f"missing columns: {missing}"
    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/hunterheyman/Claude/chicago-pipeline && source .venv/bin/activate && python -m pytest tests/test_db.py::test_init_db_adds_scoring_profile_columns -v`

Expected: FAIL with `AssertionError: missing columns: {...}` listing all 8.

- [ ] **Step 3: Add the 8 columns to `_LATER_COLUMNS["parcels"]`**

In `pipeline/db.py`, find the existing `_LATER_COLUMNS["parcels"]` tuple (around line 483). After the last entry (`("outreach_paused", "INTEGER DEFAULT 0")`), append:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_db.py::test_init_db_adds_scoring_profile_columns -v`

Expected: PASS.

- [ ] **Step 5: Run the full db test file to confirm no regression**

Run: `python -m pytest tests/test_db.py -q`

Expected: all existing tests PASS plus the new one.

- [ ] **Step 6: Commit**

```bash
git add pipeline/db.py tests/test_db.py
git commit -m "feat(db): add 8 columns to parcels for scoring-profiles spec

Pure additive migration via _LATER_COLUMNS. Columns stay NULL until
later phases populate them:
  - lot_width_ft / lot_depth_ft (Phase 2 — derived from CCGIS polygons)
  - adu_eligible / adu_restriction_text / adu_has_annual_limits
    (Phase 3 — derived from City ArcGIS ADU eligibility layer)
  - last_sale_price_recent (Phase 4 — derived in score.py)
  - score_adu / score_redev (Phase 4 — written by multi-profile engine)

Per the spec, scoring weights are not activated until Phase 4 ships
and Hunter approves the proposed weights.

Spec: docs/superpowers/specs/2026-06-08-scoring-adu-redev-design.md"
```

---

# Phase 2: Lot width / depth backfill

Extend `sources/ccgis_parcels.py` to compute the minimum rotated rectangle of each polygon and persist width + depth alongside the existing area.

## Task 2.1: Add `width_ft` + `depth_ft` columns to `raw_ccgis_parcels` schema

**Files:**
- Modify: `pipeline/db.py` (the `raw_ccgis_parcels` CREATE TABLE around line 376; also add to `_LATER_COLUMNS` if pre-existing tables need ALTER)
- Test: `tests/test_db.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_db.py`:

```python
def test_raw_ccgis_parcels_has_width_depth_columns(tmp_path):
    from pipeline.db import init_db
    import sqlite3
    p = tmp_path / "t.db"
    init_db(p)
    conn = sqlite3.connect(p)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(raw_ccgis_parcels)")}
    assert "width_ft" in cols
    assert "depth_ft" in cols
    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_db.py::test_raw_ccgis_parcels_has_width_depth_columns -v`

Expected: FAIL with `AssertionError` on `assert "width_ft" in cols`.

- [ ] **Step 3: Add the columns**

In `pipeline/db.py`, find `CREATE TABLE IF NOT EXISTS raw_ccgis_parcels` (around line 376). Change the body from:

```sql
CREATE TABLE IF NOT EXISTS raw_ccgis_parcels (
    pin10 TEXT PRIMARY KEY,
    area_sf REAL,
    fetched_at TEXT
);
```

to:

```sql
CREATE TABLE IF NOT EXISTS raw_ccgis_parcels (
    pin10 TEXT PRIMARY KEY,
    area_sf REAL,
    width_ft REAL,
    depth_ft REAL,
    fetched_at TEXT
);
```

Also add to `_LATER_COLUMNS` (for upgrading pre-existing DBs without recreation):

```python
    "raw_ccgis_parcels": (
        ("width_ft", "REAL"),
        ("depth_ft", "REAL"),
    ),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_db.py::test_raw_ccgis_parcels_has_width_depth_columns -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pipeline/db.py tests/test_db.py
git commit -m "feat(db): raw_ccgis_parcels gains width_ft + depth_ft columns

Phase 2 of scoring-profiles plan. Both CREATE TABLE and _LATER_COLUMNS
updated so fresh and pre-existing DBs both get the columns."
```

## Task 2.2: Implement `_polygon_to_width_depth` helper (pure function, TDD)

**Files:**
- Modify: `sources/ccgis_parcels.py`
- Test: `tests/test_source_ccgis_parcels.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_source_ccgis_parcels.py`:

```python
from shapely.geometry import Polygon


def test_polygon_to_width_depth_rectangular():
    """A 25 ft × 125 ft rectangle (standard Chicago lot) returns
    (width=25, depth=125) within 0.5 ft tolerance."""
    from sources.ccgis_parcels import _polygon_to_width_depth
    rect = Polygon([(0, 0), (25, 0), (25, 125), (0, 125)])
    width, depth = _polygon_to_width_depth(rect)
    assert abs(width - 25.0) < 0.5
    assert abs(depth - 125.0) < 0.5


def test_polygon_to_width_depth_irregular_does_not_crash():
    """L-shaped polygon: function returns the minimum rotated rectangle
    dimensions (slightly over-stating the parcel's usable width). Acceptable
    per the spec — we just need it not to crash."""
    from sources.ccgis_parcels import _polygon_to_width_depth
    l_shape = Polygon([(0, 0), (30, 0), (30, 30), (20, 30), (20, 100), (0, 100)])
    width, depth = _polygon_to_width_depth(l_shape)
    assert width > 0
    assert depth > 0
    assert depth >= width  # depth is always the longer side


def test_polygon_to_width_depth_rotated():
    """A rectangle rotated 30 degrees should still report (width, depth)
    correctly — minimum_rotated_rectangle aligns to the polygon, not the axes."""
    from sources.ccgis_parcels import _polygon_to_width_depth
    import math
    # 30 ft × 100 ft rectangle, rotated 30°
    angle = math.radians(30)
    cos, sin = math.cos(angle), math.sin(angle)
    corners = [(0, 0), (30, 0), (30, 100), (0, 100)]
    rotated = [(x * cos - y * sin, x * sin + y * cos) for x, y in corners]
    rect = Polygon(rotated)
    width, depth = _polygon_to_width_depth(rect)
    assert abs(width - 30.0) < 0.5
    assert abs(depth - 100.0) < 0.5
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_source_ccgis_parcels.py::test_polygon_to_width_depth_rectangular -v`

Expected: FAIL with `ImportError` — function doesn't exist yet.

- [ ] **Step 3: Implement `_polygon_to_width_depth`**

In `sources/ccgis_parcels.py`, after the `PLANAR_CRS` constant (around line 40), add:

```python
def _polygon_to_width_depth(geom) -> tuple[float, float]:
    """Compute (width, depth) of a polygon in its native CRS units by
    taking the side lengths of its minimum rotated rectangle.

    Width is always the shorter side; depth the longer. For Chicago lots
    in EPSG:3435 (US survey feet) this is street frontage × lot depth.

    For non-rectangular polygons (L-shape, wedge, corner), the minimum
    rotated rectangle is an over-bounding rectangle, so width slightly
    over-states the true narrow dimension. Acceptable for scoring per
    the design spec — ordering of candidates matters, not exact dimensions.
    """
    mbr = geom.minimum_rotated_rectangle
    if mbr.is_empty or mbr.geom_type != "Polygon":
        return (0.0, 0.0)
    coords = list(mbr.exterior.coords)
    if len(coords) < 4:
        return (0.0, 0.0)
    # First three corners give us two adjacent sides.
    p0, p1, p2 = coords[0], coords[1], coords[2]
    side_a = ((p1[0] - p0[0]) ** 2 + (p1[1] - p0[1]) ** 2) ** 0.5
    side_b = ((p2[0] - p1[0]) ** 2 + (p2[1] - p1[1]) ** 2) ** 0.5
    return (min(side_a, side_b), max(side_a, side_b))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_source_ccgis_parcels.py -k polygon_to_width -v`

Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add sources/ccgis_parcels.py tests/test_source_ccgis_parcels.py
git commit -m "feat(ccgis): add _polygon_to_width_depth helper

Pure function: takes a shapely polygon in any planar CRS, returns
(width, depth) in CRS units from its minimum rotated rectangle.

Width is always the shorter side, depth the longer. For Chicago lots
this is street frontage × lot depth. Handles irregular shapes
gracefully (returns bounding-box approximation, doesn't crash)."
```

## Task 2.3: Wire `_polygon_to_width_depth` into `ccgis_parcels.fetch()` and persist

**Files:**
- Modify: `sources/ccgis_parcels.py` (the `fetch` function around line 43–117)
- Test: `tests/test_source_ccgis_parcels.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_source_ccgis_parcels.py` (alongside `test_ccgis_parcels_writes_lot_size_from_polygon_area`):

```python
@responses.activate
def test_ccgis_parcels_writes_lot_width_and_depth(db_path, geo, cook_client):
    """After fetch, parcels.lot_width_ft and parcels.lot_depth_ft are
    populated for every pin sharing a fetched polygon."""
    _seed_through_characteristics(db_path, geo, cook_client)

    fx = json.loads((FIXTURES / "ccgis_parcels.json").read_text())
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{ccgis_parcels.DATASET_ID}.json",
        json=fx, status=200)
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{ccgis_parcels.DATASET_ID}.json",
        json=[], status=200)
    ccgis_parcels.fetch(geo, db_path, cook_client)

    conn = get_connection(db_path)
    rows = conn.execute(
        "SELECT pin, lot_width_ft, lot_depth_ft FROM parcels "
        "WHERE lot_width_ft IS NOT NULL"
    ).fetchall()
    assert len(rows) >= 1, "expected at least one parcel with lot_width_ft populated"
    for r in rows:
        assert r["lot_width_ft"] > 0
        assert r["lot_depth_ft"] >= r["lot_width_ft"]
    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_source_ccgis_parcels.py::test_ccgis_parcels_writes_lot_width_and_depth -v`

Expected: FAIL with `assert len(rows) >= 1` — column exists but is NULL.

- [ ] **Step 3: Update `fetch()` to compute and persist width/depth**

In `sources/ccgis_parcels.py`, find the `fetch` function. After the existing line:

```python
    gdf["area_sf"] = gdf.geometry.area
```

(around line 76) add:

```python
    # Per the scoring-profiles spec, also derive (width, depth) from the
    # minimum rotated rectangle. Cheap to compute alongside area.
    width_depth = gdf.geometry.apply(_polygon_to_width_depth)
    gdf["width_ft"] = width_depth.map(lambda wd: wd[0])
    gdf["depth_ft"] = width_depth.map(lambda wd: wd[1])
```

Change the `raw_rows` construction (around line 81) from:

```python
    raw_rows = [
        {"pin10": row["pin10"], "area_sf": float(row["area_sf"]),
         "fetched_at": fetched_at}
        for _, row in gdf.iterrows()
    ]
```

to:

```python
    raw_rows = [
        {"pin10": row["pin10"], "area_sf": float(row["area_sf"]),
         "width_ft": float(row["width_ft"]),
         "depth_ft": float(row["depth_ft"]),
         "fetched_at": fetched_at}
        for _, row in gdf.iterrows()
    ]
```

Change the parcels UPDATE (around line 95) from:

```python
        for row in raw_rows:
            conn.execute(
                "UPDATE parcels SET lot_size_sf = :lot_sf, last_updated_date = :now "
                "WHERE pin10 = :p10",
                {"lot_sf": row["area_sf"], "now": fetched_at, "p10": row["pin10"]},
            )
```

to:

```python
        for row in raw_rows:
            conn.execute(
                "UPDATE parcels SET lot_size_sf = :lot_sf, "
                "  lot_width_ft = :width, lot_depth_ft = :depth, "
                "  last_updated_date = :now "
                "WHERE pin10 = :p10",
                {"lot_sf": row["area_sf"], "width": row["width_ft"],
                 "depth": row["depth_ft"], "now": fetched_at, "p10": row["pin10"]},
            )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_source_ccgis_parcels.py -v`

Expected: All tests PASS (new + existing).

- [ ] **Step 5: Run full test suite to ensure no regression**

Run: `python -m pytest -q`

Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add sources/ccgis_parcels.py tests/test_source_ccgis_parcels.py
git commit -m "feat(ccgis): persist lot_width_ft + lot_depth_ft to parcels

Compute the minimum rotated rectangle alongside area at fetch time.
Width = shorter side, depth = longer. Stored in raw_ccgis_parcels for
audit + written to parcels for scoring use.

Existing area_sf math unchanged. Backfill required: re-run
\`python -m pipeline.fetch_all --source ccgis_parcels\` over the
target geography to populate the new columns on existing DBs.

Phase 2 of the scoring-profiles spec."
```

## Task 2.4: Backfill lot_width_ft / lot_depth_ft on Hunter's local DB

**This task is manual — runs the actual one-time data refresh on `data/full.alt.db`.** Not part of CI.

- [ ] **Step 1: Run the backfill**

```bash
cd /Users/hunterheyman/Claude/chicago-pipeline
source .venv/bin/activate
python -m pipeline.fetch_all --source ccgis_parcels --db data/full.alt.db
```

Expected: ~30 minutes wall time. Final line: `ccgis_parcels: <N> rows`.

- [ ] **Step 2: Spot-check 5 known parcels**

```bash
python -c "
import sqlite3
con = sqlite3.connect('data/full.alt.db')
con.row_factory = sqlite3.Row
print('Sample of populated lot_width_ft values:')
for r in con.execute('SELECT pin, address, lot_size_sf, lot_width_ft, lot_depth_ft FROM parcels WHERE lot_width_ft IS NOT NULL ORDER BY RANDOM() LIMIT 5'):
    print(f'  {dict(r)}')
print()
print('Coverage:')
total = con.execute('SELECT COUNT(*) FROM parcels').fetchone()[0]
populated = con.execute('SELECT COUNT(*) FROM parcels WHERE lot_width_ft IS NOT NULL').fetchone()[0]
print(f'  {populated} / {total} parcels have lot_width_ft populated')
"
```

Expected: 5 rows with sensible width/depth values (Chicago lots typically width 20–50 ft, depth 100–150 ft). Coverage typically 90%+ — some parcels lack GIS polygons.

**Gate before Phase 3:** Hunter eyeballs the 5 sampled parcels and confirms the width values look plausible against neighborhood knowledge.

---

# Phase 3: ADU eligibility enrichment

Add a new source that fetches the City's ADU-eligibility polygons, spatial-joins to each parcel's centroid, and derives the three ADU columns.

## Task 3.1: Add `raw_chicago_adu_zones` table schema

**Files:**
- Modify: `pipeline/db.py` (SCHEMA_SQL section, after other `raw_*` tables)
- Test: `tests/test_db.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_db.py`:

```python
def test_raw_chicago_adu_zones_table_exists(tmp_path):
    from pipeline.db import init_db
    import sqlite3
    p = tmp_path / "t.db"
    init_db(p)
    conn = sqlite3.connect(p)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(raw_chicago_adu_zones)")}
    expected = {"zone_id", "adu_area_code", "restriction_text",
                "polygon_wkt", "fetched_at"}
    assert expected.issubset(cols), f"missing: {expected - cols}"
    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_db.py::test_raw_chicago_adu_zones_table_exists -v`

Expected: FAIL with `AssertionError: missing: {...}`.

- [ ] **Step 3: Add the table to SCHEMA_SQL**

In `pipeline/db.py`, find the section with other raw tables (e.g. `raw_ccgis_parcels` definition around line 376). After it, add:

```sql
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_db.py::test_raw_chicago_adu_zones_table_exists -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pipeline/db.py tests/test_db.py
git commit -m "feat(db): add raw_chicago_adu_zones table

Storage for the City's ADU eligibility polygons. Phase 3 of the
scoring-profiles spec. The new chicago_adu_zones source will populate
this and use the polygons to derive parcels.adu_eligible."
```

## Task 3.2: Implement `derive_adu_eligible` pure function

**Files:**
- Create: `sources/chicago_adu_zones.py` (initial stub with just the derivation logic)
- Create: `tests/test_source_chicago_adu_zones.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_source_chicago_adu_zones.py`:

```python
import pytest

from sources.chicago_adu_zones import derive_adu_eligible


@pytest.mark.parametrize("zone_class,in_rs_polygon,expected", [
    # RT/RM/B/C1/C2 are eligible everywhere — polygon flag irrelevant
    ("RT-3.5", False, 1),
    ("RT-3.5", True, 1),
    ("RT-4", False, 1),
    ("RM-5", False, 1),
    ("RM-6.5", True, 1),
    ("B3-2", False, 1),
    ("B1-2", True, 1),
    ("C1-2", False, 1),
    ("C2-3", True, 1),
    # RS zones depend on the polygon containment
    ("RS-1", True, 1),
    ("RS-1", False, 0),
    ("RS-2", True, 1),
    ("RS-2", False, 0),
    ("RS-3", True, 1),
    ("RS-3", False, 0),
    # Not eligible — anywhere
    ("M1-2", False, 0),
    ("M1-2", True, 0),
    ("PD 853", False, 0),
    ("C3-2", False, 0),     # C3+ is NOT in the C1/C2 allowlist
    ("C4-3", True, 0),
    # Edge cases
    (None, False, 0),
    (None, True, 0),
    ("", False, 0),
])
def test_derive_adu_eligible(zone_class, in_rs_polygon, expected):
    assert derive_adu_eligible(zone_class, in_rs_polygon) == expected


def test_derive_adu_eligible_handles_case_insensitive_zone():
    """Real assessor data has mixed casing; the rule should be case-insensitive."""
    assert derive_adu_eligible("rt-3", False) == 1
    assert derive_adu_eligible("Rs-3", True) == 1
    assert derive_adu_eligible("rs-3", False) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_source_chicago_adu_zones.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'sources.chicago_adu_zones'`.

- [ ] **Step 3: Create the module with the derivation function**

Create `sources/chicago_adu_zones.py`:

```python
"""Source: City of Chicago ADU Eligibility Map.

Fetches polygons from the City's ArcGIS REST endpoint:
  https://services7.arcgis.com/A03QrhyHnDaUmK0W/arcgis/rest/services/ADUAllowedRS2AA_view/FeatureServer/0

Each polygon represents an RS-zoned area where ADUs are allowed (the
2021 ordinance restricts RS-zoned ADUs to these designated areas).
Each polygon also carries a `Text` field describing the per-area
restrictions (annual block caps, owner-occupancy requirements, etc.).

This source:
  1. Fetches the polygons into raw_chicago_adu_zones.
  2. Spatial-joins each parcel's centroid against the polygons.
  3. Derives parcels.adu_eligible + parcels.adu_restriction_text +
     parcels.adu_has_annual_limits.

Refresh cadence: monthly. The City publishes updates infrequently;
operators can re-run this source at will without affecting other data.
"""
from __future__ import annotations


# Zone-class prefixes that are ADU-eligible citywide (no polygon lookup
# needed). Per the City's instructions:
#   "If the zoning is RT, RM, any B, C1 or C2 — you are eligible for an ADU!"
CITYWIDE_ELIGIBLE_PREFIXES = ("RT-", "RM-", "B", "C1-", "C2-")

# RS zones are conditionally eligible — only when inside an ADU-Allowed RS polygon.
RS_ZONES = ("RS-1", "RS-2", "RS-3")


def derive_adu_eligible(zone_class: str | None, in_rs_polygon: bool) -> int:
    """Return 1 if a parcel with this zone is ADU-eligible, else 0.

    Rules (from chicago.gov/adu instructions):
      - RT-*, RM-*, B*, C1-*, C2-* → eligible citywide (1)
      - RS-1, RS-2, RS-3 → eligible only if inside an ADU-Allowed RS polygon
      - Everything else (M-*, PD, C3+, etc.) → not eligible (0)
    """
    z = (zone_class or "").upper()
    if not z:
        return 0
    if z.startswith(CITYWIDE_ELIGIBLE_PREFIXES):
        return 1
    if z in RS_ZONES:
        return 1 if in_rs_polygon else 0
    return 0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_source_chicago_adu_zones.py -v`

Expected: All 22+ parametrized cases PASS plus the case-insensitive test.

- [ ] **Step 5: Commit**

```bash
git add sources/chicago_adu_zones.py tests/test_source_chicago_adu_zones.py
git commit -m "feat(adu): pure derive_adu_eligible function

Implements the City's ADU eligibility rule:
  - RT-*, RM-*, B*, C1-*, C2-*: eligible citywide
  - RS-1/2/3: eligible only when inside an ADU-Allowed RS polygon
  - All other zones (M-*, PD, C3+, etc.): not eligible

Case-insensitive; null and empty zones return 0.

Phase 3 of the scoring-profiles spec."
```

## Task 3.3: Implement the ArcGIS polygon fetch

**Files:**
- Modify: `sources/chicago_adu_zones.py`
- Create: `tests/fixtures/chicago_adu_zones.json` (small recorded fixture)
- Modify: `tests/test_source_chicago_adu_zones.py`

- [ ] **Step 1: Create the fixture from a real query**

```bash
curl -s 'https://services7.arcgis.com/A03QrhyHnDaUmK0W/arcgis/rest/services/ADUAllowedRS2AA_view/FeatureServer/0/query?where=1%3D1&outFields=ADU_Area,Zone,Text&returnGeometry=true&outSR=4326&resultRecordCount=3&f=json' \
  > tests/fixtures/chicago_adu_zones.json
```

Verify the file is valid JSON with at least 1 feature:

```bash
python -c "import json; d = json.load(open('tests/fixtures/chicago_adu_zones.json')); print(f'features: {len(d.get(\"features\", []))}'); print(d['features'][0]['attributes'])"
```

Expected: `features: 3` and an attributes dict with `ADU_Area`, `Zone`, `Text`.

- [ ] **Step 2: Write the failing fetch test**

Add to `tests/test_source_chicago_adu_zones.py`:

```python
import json
import responses
from pathlib import Path

from pipeline.db import init_db, get_connection


FIXTURES = Path(__file__).parent / "fixtures"


@responses.activate
def test_fetch_writes_polygons_to_raw_table(tmp_path):
    """Fetching the ArcGIS layer persists each feature as a row in
    raw_chicago_adu_zones with zone_id, restriction_text, polygon_wkt."""
    from sources.chicago_adu_zones import fetch, ARCGIS_QUERY_URL

    db_path = tmp_path / "t.db"
    init_db(db_path)

    fx = json.loads((FIXTURES / "chicago_adu_zones.json").read_text())
    responses.add(responses.GET, ARCGIS_QUERY_URL, json=fx, status=200)

    n = fetch(db_path)
    assert n >= 1, "should write at least one polygon row"

    conn = get_connection(db_path)
    rows = conn.execute(
        "SELECT zone_id, restriction_text, polygon_wkt FROM raw_chicago_adu_zones"
    ).fetchall()
    assert len(rows) == n
    for r in rows:
        assert r["zone_id"]
        assert r["polygon_wkt"].startswith("POLYGON") or r["polygon_wkt"].startswith("MULTIPOLYGON")
    conn.close()
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_source_chicago_adu_zones.py::test_fetch_writes_polygons_to_raw_table -v`

Expected: FAIL with `ImportError: cannot import name 'fetch'`.

- [ ] **Step 4: Implement `fetch`**

Add to `sources/chicago_adu_zones.py`:

```python
from datetime import datetime, UTC
from pathlib import Path

import requests
from shapely.geometry import shape

from pipeline.db import get_connection


ARCGIS_QUERY_URL = (
    "https://services7.arcgis.com/A03QrhyHnDaUmK0W/arcgis/rest/services/"
    "ADUAllowedRS2AA_view/FeatureServer/0/query"
)


def _query_params() -> dict:
    return {
        "where": "1=1",
        "outFields": "ADU_Area,Zone,Text",
        "returnGeometry": "true",
        "outSR": "4326",        # WGS84 — same CRS as parcels.lat/lng
        "f": "json",
    }


def fetch(db_path: Path) -> int:
    """Fetch all polygons from the City's ADU eligibility layer, persist
    to raw_chicago_adu_zones. Returns the number of polygons written.

    Idempotent: zone_id is the primary key, so re-running upserts in place.
    Safe to run as a monthly refresh — the City's polygons rarely change.
    """
    fetched_at = datetime.now(UTC).isoformat(timespec="seconds")
    resp = requests.get(ARCGIS_QUERY_URL, params=_query_params(), timeout=30)
    resp.raise_for_status()
    data = resp.json()
    features = data.get("features", []) or []

    rows = []
    for f in features:
        attrs = f.get("attributes", {}) or {}
        geom_dict = f.get("geometry")
        if not geom_dict:
            continue
        # ArcGIS REST returns rings in the 'rings' key for polygon geometry.
        # Convert to GeoJSON polygon shape that shapely understands.
        rings = geom_dict.get("rings")
        if not rings:
            continue
        geojson = {"type": "Polygon", "coordinates": rings}
        try:
            geom = shape(geojson)
        except Exception:
            continue
        zone_id = str(attrs.get("Zone") or "")
        if not zone_id:
            continue
        rows.append({
            "zone_id": zone_id,
            "adu_area_code": attrs.get("ADU_Area") or "",
            "restriction_text": attrs.get("Text") or "",
            "polygon_wkt": geom.wkt,
            "fetched_at": fetched_at,
        })

    conn = get_connection(db_path)
    try:
        for r in rows:
            conn.execute(
                "INSERT OR REPLACE INTO raw_chicago_adu_zones "
                "(zone_id, adu_area_code, restriction_text, polygon_wkt, fetched_at) "
                "VALUES (:zone_id, :adu_area_code, :restriction_text, "
                ":polygon_wkt, :fetched_at)",
                r,
            )
        conn.commit()
    finally:
        conn.close()
    return len(rows)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_source_chicago_adu_zones.py -v`

Expected: All tests PASS.

- [ ] **Step 6: Commit**

```bash
git add sources/chicago_adu_zones.py tests/test_source_chicago_adu_zones.py tests/fixtures/chicago_adu_zones.json
git commit -m "feat(adu): fetch polygons from City ArcGIS layer

fetch() pulls all ADU-Allowed RS Area polygons from the City's
ArcGIS REST endpoint (services7.arcgis.com/.../ADUAllowedRS2AA_view).
Persists to raw_chicago_adu_zones with polygon stored as WKT for
later shapely-based spatial joins.

Idempotent via zone_id PRIMARY KEY. Monthly refresh cadence."
```

## Task 3.4: Implement spatial join + write parcels.adu_eligible / adu_restriction_text / adu_has_annual_limits

**Files:**
- Modify: `sources/chicago_adu_zones.py`
- Modify: `tests/test_source_chicago_adu_zones.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_source_chicago_adu_zones.py`:

```python
@responses.activate
def test_apply_to_parcels_sets_adu_eligible_and_restriction_text(tmp_path):
    """After fetch + apply_to_parcels:
      - RT/RM/B/C1/C2 parcels: adu_eligible=1, restriction_text=NULL (citywide)
      - RS parcels inside a polygon: adu_eligible=1, restriction_text=polygon's Text
      - RS parcels outside polygons: adu_eligible=0, restriction_text=NULL
      - M/PD/etc. parcels: adu_eligible=0, restriction_text=NULL
      - adu_has_annual_limits is 1 iff restriction_text contains 'Annual Limits'.
    """
    from sources.chicago_adu_zones import fetch, apply_to_parcels, ARCGIS_QUERY_URL

    db_path = tmp_path / "t.db"
    init_db(db_path)

    # Seed parcels with known coords. The fixture's polygons cover specific
    # geographic areas; we craft parcels that fall inside vs outside.
    fx = json.loads((FIXTURES / "chicago_adu_zones.json").read_text())
    responses.add(responses.GET, ARCGIS_QUERY_URL, json=fx, status=200)
    fetch(db_path)

    # Get a point that's INSIDE the first polygon for the RS-in-polygon test.
    from shapely import wkt as wkt_lib
    conn = get_connection(db_path)
    first_polygon_wkt = conn.execute(
        "SELECT polygon_wkt FROM raw_chicago_adu_zones LIMIT 1"
    ).fetchone()["polygon_wkt"]
    inside = wkt_lib.loads(first_polygon_wkt).representative_point()
    inside_lat, inside_lng = inside.y, inside.x

    # Insert test parcels covering all eligibility scenarios.
    test_parcels = [
        # (pin, zone_class, lat, lng, expected_eligible, expected_restriction_substr)
        ("00000000000001", "RT-4",  41.95, -87.65, 1, None),       # citywide-eligible
        ("00000000000002", "B3-2",  41.95, -87.65, 1, None),       # citywide-eligible
        ("00000000000003", "C1-2",  41.95, -87.65, 1, None),       # citywide-eligible
        ("00000000000004", "RS-3",  inside_lat, inside_lng, 1, "set"),  # RS inside polygon
        ("00000000000005", "RS-3",  41.95, -87.65, 0, None),       # RS outside any polygon
        ("00000000000006", "M1-2",  41.95, -87.65, 0, None),       # not eligible
        ("00000000000007", "PD 853", 41.95, -87.65, 0, None),      # not eligible
    ]
    for pin, zc, lat, lng, _, _ in test_parcels:
        conn.execute(
            "INSERT INTO parcels(pin, pin10, zone_class, lat, lng) "
            "VALUES (?, ?, ?, ?, ?)",
            (pin, pin[:10], zc, lat, lng),
        )
    conn.commit()
    conn.close()

    apply_to_parcels(db_path)

    conn = get_connection(db_path)
    for pin, _, _, _, expected_eligible, expected_restriction in test_parcels:
        row = conn.execute(
            "SELECT adu_eligible, adu_restriction_text, adu_has_annual_limits "
            "FROM parcels WHERE pin=?", (pin,)
        ).fetchone()
        assert row["adu_eligible"] == expected_eligible, \
            f"pin {pin}: expected adu_eligible={expected_eligible}, got {row['adu_eligible']}"
        if expected_restriction is None:
            assert row["adu_restriction_text"] is None, \
                f"pin {pin}: expected NULL restriction, got {row['adu_restriction_text']!r}"
        else:
            assert row["adu_restriction_text"], \
                f"pin {pin}: expected restriction text to be set"
    conn.close()


@responses.activate
def test_apply_to_parcels_sets_has_annual_limits_flag(tmp_path):
    """adu_has_annual_limits is derived from restriction_text containing
    'Annual Limits'."""
    from sources.chicago_adu_zones import fetch, apply_to_parcels, ARCGIS_QUERY_URL
    db_path = tmp_path / "t.db"
    init_db(db_path)
    fx = json.loads((FIXTURES / "chicago_adu_zones.json").read_text())
    responses.add(responses.GET, ARCGIS_QUERY_URL, json=fx, status=200)
    fetch(db_path)

    # Pick the polygon that has 'Annual Limits' in restriction_text;
    # craft a parcel inside it.
    from shapely import wkt as wkt_lib
    conn = get_connection(db_path)
    row = conn.execute(
        "SELECT polygon_wkt FROM raw_chicago_adu_zones "
        "WHERE restriction_text LIKE '%Annual Limits%' LIMIT 1"
    ).fetchone()
    if row is None:
        pytest.skip("fixture has no polygon with 'Annual Limits'")
    pt = wkt_lib.loads(row["polygon_wkt"]).representative_point()
    conn.execute(
        "INSERT INTO parcels(pin, pin10, zone_class, lat, lng) "
        "VALUES ('00000000000010', '0000000000', 'RS-3', ?, ?)",
        (pt.y, pt.x),
    )
    conn.commit()
    conn.close()

    apply_to_parcels(db_path)

    conn = get_connection(db_path)
    flag = conn.execute(
        "SELECT adu_has_annual_limits FROM parcels WHERE pin='00000000000010'"
    ).fetchone()[0]
    assert flag == 1
    conn.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_source_chicago_adu_zones.py -v`

Expected: `apply_to_parcels` tests FAIL with `ImportError`.

- [ ] **Step 3: Implement `apply_to_parcels`**

Add to `sources/chicago_adu_zones.py`:

```python
from shapely.geometry import Point
from shapely import wkt as wkt_lib


def apply_to_parcels(db_path: Path) -> int:
    """For every parcel with a (lat, lng), determine ADU eligibility:
      - parcels.adu_eligible: 1 if zone_class is citywide-eligible OR
        (zone_class is RS-1/2/3 AND centroid is inside any ADU-Allowed
        RS polygon). 0 otherwise.
      - parcels.adu_restriction_text: the Text field of the containing
        polygon (for RS-in-polygon cases); NULL for citywide-eligible
        and non-eligible parcels.
      - parcels.adu_has_annual_limits: 1 if restriction_text contains
        'Annual Limits', else 0.

    Returns the number of parcels updated.

    Implementation: load all polygons into memory (small — typically <100
    polygons), iterate parcels and test point-in-polygon. shapely's
    contains() is exact; tens of thousands of point-vs-polygon tests
    complete in seconds.
    """
    conn = get_connection(db_path)
    try:
        # Load polygons (small set, fits in memory comfortably).
        polygons = [
            {
                "zone_id": r["zone_id"],
                "restriction_text": r["restriction_text"],
                "geom": wkt_lib.loads(r["polygon_wkt"]),
            }
            for r in conn.execute(
                "SELECT zone_id, restriction_text, polygon_wkt "
                "FROM raw_chicago_adu_zones"
            )
        ]

        # Iterate parcels with coords. Skip ones without lat/lng.
        parcels = conn.execute(
            "SELECT pin, zone_class, lat, lng FROM parcels "
            "WHERE lat IS NOT NULL AND lng IS NOT NULL"
        ).fetchall()

        n_updated = 0
        for p in parcels:
            pt = Point(p["lng"], p["lat"])
            # Only check polygon containment for RS zones — the rest are
            # citywide-eligible or not-eligible regardless of polygons.
            z = (p["zone_class"] or "").upper()
            in_rs_polygon = False
            restriction_text = None
            if z in RS_ZONES:
                for poly in polygons:
                    if poly["geom"].contains(pt):
                        in_rs_polygon = True
                        restriction_text = poly["restriction_text"] or None
                        break

            eligible = derive_adu_eligible(p["zone_class"], in_rs_polygon)
            has_annual_limits = 1 if (
                restriction_text and "Annual Limits" in restriction_text
            ) else 0
            conn.execute(
                "UPDATE parcels SET "
                "  adu_eligible = ?, "
                "  adu_restriction_text = ?, "
                "  adu_has_annual_limits = ? "
                "WHERE pin = ?",
                (eligible, restriction_text, has_annual_limits, p["pin"]),
            )
            n_updated += 1
        conn.commit()
    finally:
        conn.close()
    return n_updated
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_source_chicago_adu_zones.py -v`

Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add sources/chicago_adu_zones.py tests/test_source_chicago_adu_zones.py
git commit -m "feat(adu): spatial-join polygons to parcels + write ADU columns

apply_to_parcels iterates every parcel with (lat, lng), tests
RS-zoned parcels against the loaded polygons, derives:
  - parcels.adu_eligible (1/0)
  - parcels.adu_restriction_text (polygon Text for RS-in-polygon; NULL else)
  - parcels.adu_has_annual_limits (1 if restriction_text has 'Annual Limits')

Citywide-eligible (RT/RM/B/C1/C2) and non-eligible (M/PD/C3+) zones
skip the polygon scan — only RS-1/2/3 needs the containment test."
```

## Task 3.5: Register `chicago_adu_zones` in `pipeline/fetch_all.py`

**Files:**
- Modify: `pipeline/fetch_all.py`

- [ ] **Step 1: Identify the registration point**

Run: `grep -n "ccgis_parcels\|cdp_zoning\|results.append" pipeline/fetch_all.py | head -10`

Expected output identifies the section where individual sources are registered (similar to how `ccgis_parcels` gets registered).

- [ ] **Step 2: Add the import and registration**

In `pipeline/fetch_all.py`, find the existing import block (around line 22 per spec context):

```python
from sources import (
    ...
    ccgis_parcels,
    ...
)
```

Add `chicago_adu_zones` to that list.

Find the existing call that runs ccgis_parcels (around line 99):

```python
results.append(_run("ccgis_parcels", ccgis_parcels.fetch, db_path, geo, db_path, cook))
```

After all other sources have been registered, append:

```python
    # ADU eligibility — depends on parcels having (lat, lng), which all
    # prior sources populate. Two-step: fetch polygons, then spatial-join
    # to parcels.
    results.append(_run("chicago_adu_zones (fetch)",
                        chicago_adu_zones.fetch, db_path, db_path))
    results.append(_run("chicago_adu_zones (apply)",
                        chicago_adu_zones.apply_to_parcels, db_path, db_path))
```

(Note: the `_run` helper's signature in `fetch_all.py` may differ slightly — match the existing pattern. The two key invocations are `fetch(db_path)` and `apply_to_parcels(db_path)`.)

- [ ] **Step 3: Run the test suite to confirm no regression**

Run: `python -m pytest -q`

Expected: All tests PASS. (The orchestrator is integration-tested in `tests/test_fetch_all.py` if present; if not, just verify nothing in the suite broke.)

- [ ] **Step 4: Commit**

```bash
git add pipeline/fetch_all.py
git commit -m "feat(fetch): register chicago_adu_zones in fetch_all

Runs after the parcel + GIS sources so the spatial join has accurate
(lat, lng) for every parcel. Two-step: fetch polygons → apply_to_parcels.

Phase 3 of the scoring-profiles spec."
```

## Task 3.6: Run the actual ADU enrichment over `data/full.alt.db`

**This task is manual.** Not part of CI.

- [ ] **Step 1: Fetch polygons + apply**

```bash
source .venv/bin/activate
python -c "
from pathlib import Path
from sources.chicago_adu_zones import fetch, apply_to_parcels
db = Path('data/full.alt.db')
n = fetch(db)
print(f'fetched {n} polygons')
m = apply_to_parcels(db)
print(f'updated {m} parcels')
"
```

Expected: "fetched N polygons" (N typically 10–50) and "updated M parcels" (M ≈ all parcels with lat/lng).

- [ ] **Step 2: Spot-check distribution**

```bash
python -c "
import sqlite3
con = sqlite3.connect('data/full.alt.db')
print('adu_eligible distribution:')
for v, n in con.execute('SELECT adu_eligible, COUNT(*) FROM parcels GROUP BY adu_eligible'):
    print(f'  {v!r}: {n}')
print()
print('Sample RS-in-polygon parcels:')
for r in con.execute(\"\"\"
  SELECT pin, zone_class, address, adu_eligible, adu_restriction_text, adu_has_annual_limits
  FROM parcels WHERE adu_restriction_text IS NOT NULL LIMIT 5
\"\"\"):
    print(f'  {dict(zip([d[0] for d in con.execute(\"SELECT pin, zone_class, address, adu_eligible, adu_restriction_text, adu_has_annual_limits FROM parcels LIMIT 0\").description], r))}')
"
```

Expected: a meaningful split between `adu_eligible=1` (majority — RT/RM/B/C1/C2 + RS-in-polygon) and `adu_eligible=0` (M/PD/C3+/RS-outside-polygon).

**Gate before Phase 4:** Hunter verifies an RS-zoned parcel inside a known ADU-allowed area shows `adu_eligible=1`, and one outside shows `adu_eligible=0`.

---

# Phase 4: Multi-profile scoring engine + YAMLs + filter additions

The biggest phase. Three sub-deliverables: (a) `last_sale_price_recent` derivation, (b) multi-profile scoring engine, (c) profile YAMLs + `profile_defaults.yaml` + filter schema additions.

## Task 4.1: Derive `last_sale_price_recent` in score.py pre-step

**Files:**
- Modify: `pipeline/score.py`
- Test: `tests/test_score.py` (likely existing; check)

- [ ] **Step 1: Locate existing score tests**

Run: `ls tests/test_score*.py tests/test_pipeline_score*.py 2>/dev/null`

If `tests/test_score.py` exists, add to it; otherwise create `tests/test_score.py` matching the style of `tests/test_pipeline_enrichment.py`.

- [ ] **Step 2: Write the failing test**

Add to the relevant test file:

```python
def test_derive_last_sale_price_recent_only_when_recent(tmp_path):
    """last_sale_price_recent mirrors last_sale_price WHEN
    hold_duration_years <= 3, else NULL."""
    from pipeline.db import init_db, get_connection
    from pipeline.score import derive_last_sale_price_recent

    db = tmp_path / "t.db"
    init_db(db)
    conn = get_connection(db)
    conn.executemany(
        "INSERT INTO parcels(pin, last_sale_price, hold_duration_years) "
        "VALUES (?, ?, ?)",
        [
            ("00000000000001", 500000.0, 1.5),   # recent
            ("00000000000002", 900000.0, 3.0),   # exactly 3y — recent
            ("00000000000003", 250000.0, 3.01),  # stale
            ("00000000000004", 800000.0, 10.0),  # stale
            ("00000000000005", None, 1.0),       # no sale price — NULL
            ("00000000000006", 700000.0, None),  # no hold duration — NULL
        ],
    )
    conn.commit()
    conn.close()

    derive_last_sale_price_recent(db)

    conn = get_connection(db)
    rows = {r["pin"]: r["last_sale_price_recent"] for r in conn.execute(
        "SELECT pin, last_sale_price_recent FROM parcels ORDER BY pin"
    )}
    assert rows["00000000000001"] == 500000.0
    assert rows["00000000000002"] == 900000.0
    assert rows["00000000000003"] is None
    assert rows["00000000000004"] is None
    assert rows["00000000000005"] is None
    assert rows["00000000000006"] is None
    conn.close()
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_score.py::test_derive_last_sale_price_recent_only_when_recent -v` (or whatever path you put it).

Expected: FAIL with `ImportError` for `derive_last_sale_price_recent`.

- [ ] **Step 4: Implement the derivation**

Add to `pipeline/score.py` (near the top of the module, before `score_parcels`):

```python
def derive_last_sale_price_recent(db_path: Path) -> int:
    """Write parcels.last_sale_price_recent = last_sale_price when the
    parcel transacted in the last 3 years (hold_duration_years <= 3),
    else NULL.

    Stale or missing sale-price data → NULL, which the scoring engine
    treats as the neutral 0.5 value (no penalty, no reward) on
    continuous signals. Lets the affordability signal apply only where
    we actually know what the parcel sold for recently.

    Returns the number of rows updated.
    """
    conn = get_connection(db_path)
    try:
        cur = conn.execute(
            "UPDATE parcels SET last_sale_price_recent = "
            "  CASE WHEN hold_duration_years IS NOT NULL "
            "       AND hold_duration_years <= 3 "
            "       AND last_sale_price IS NOT NULL "
            "       THEN last_sale_price "
            "       ELSE NULL END"
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_score.py::test_derive_last_sale_price_recent_only_when_recent -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add pipeline/score.py tests/test_score.py
git commit -m "feat(score): derive last_sale_price_recent column

Mirror of last_sale_price when hold_duration_years <= 3, NULL otherwise.
NULL gets neutral 0.5 normalization in the scoring engine, so the
affordability signal only applies where we have reliable recent
transaction data.

Phase 4 prerequisite for the ADU profile YAML's last_sale_price_recent
signal."
```

## Task 4.2: Refactor `score_parcels` to multi-profile output

**Files:**
- Modify: `pipeline/score.py`
- Test: `tests/test_score.py`

This task changes the engine's public signature. We need to keep the existing single-profile case working (via a compatibility shim) so no existing test breaks.

- [ ] **Step 1: Inspect the current signature**

Run: `grep -n "def score_parcels\|def load_scoring_config\|def score_parcel" pipeline/score.py`

Note the existing function shapes — you'll preserve them and add a multi-profile variant.

- [ ] **Step 2: Write the failing test for multi-profile output**

Add to `tests/test_score.py`:

```python
def test_score_parcels_multi_profile_writes_separate_columns(tmp_path):
    """Two profiles in one engine pass write to two distinct columns.
    Single-profile case stays backward-compatible (legacy `score` column)."""
    from pipeline.db import init_db, get_connection
    from pipeline.score import score_parcels_multi, load_scoring_config

    db = tmp_path / "t.db"
    init_db(db)
    conn = get_connection(db)
    conn.executemany(
        "INSERT INTO parcels(pin, pin10, lot_size_sf, is_absentee) "
        "VALUES (?, ?, ?, ?)",
        [
            ("00000000000001", "0000000000", 4000.0, 1),
            ("00000000000002", "0000000000", 10000.0, 0),
        ],
    )
    conn.commit()
    conn.close()

    # Build two trivial YAML configs in tmp_path.
    yaml_a = tmp_path / "profile_a.yaml"
    yaml_a.write_text(
        "version: t-a\n"
        "top_n: 5\n"
        "signals:\n"
        "  lot_size_sf:\n"
        "    weight: 1.0\n"
        "    kind: continuous\n"
        "    direction: positive\n"
        "    insignificant: false\n"
        "    normalization: {min: 1000, max: 12000}\n"
    )
    yaml_b = tmp_path / "profile_b.yaml"
    yaml_b.write_text(
        "version: t-b\n"
        "top_n: 5\n"
        "signals:\n"
        "  is_absentee:\n"
        "    weight: 1.0\n"
        "    kind: binary\n"
        "    direction: positive\n"
        "    insignificant: false\n"
        "    normalization: {min: 0, max: 1}\n"
    )

    score_parcels_multi(db, [
        ("a", load_scoring_config(yaml_a), "score_adu"),     # reuses column name
        ("b", load_scoring_config(yaml_b), "score_redev"),
    ])

    conn = get_connection(db)
    row1 = conn.execute(
        "SELECT score_adu, score_redev FROM parcels WHERE pin='00000000000001'"
    ).fetchone()
    row2 = conn.execute(
        "SELECT score_adu, score_redev FROM parcels WHERE pin='00000000000002'"
    ).fetchone()
    # Profile A favors bigger lot → row2 > row1 on score_adu
    assert row2["score_adu"] > row1["score_adu"]
    # Profile B favors absentee → row1 > row2 on score_redev
    assert row1["score_redev"] > row2["score_redev"]
    conn.close()
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_score.py::test_score_parcels_multi_profile_writes_separate_columns -v`

Expected: FAIL with `ImportError` for `score_parcels_multi`.

- [ ] **Step 4: Implement `score_parcels_multi`**

Add to `pipeline/score.py` (after the existing `score_parcels` function):

```python
def score_parcels_multi(
    db_path: Path,
    profile_configs: list[tuple[str, ScoringConfig, str]],
) -> dict[str, int]:
    """Run scoring over every parcel for each registered profile.

    profile_configs: list of (profile_name, ScoringConfig, score_column_name).
    Each profile writes its computed score to the named column. Engine
    iterates parcels once per profile (separate UPDATE statements) —
    simple, easy to debug, fine for the 67k-parcel scale.

    Returns {profile_name: n_parcels_scored}.

    Per the spec, this engine does NOT filter parcels. Every parcel gets
    every profile's score. Filtering happens in the UI layer at query time.
    """
    counts: dict[str, int] = {}
    for profile_name, scoring_config, column in profile_configs:
        n = _score_one_profile(db_path, scoring_config, column)
        counts[profile_name] = n
    return counts


def _score_one_profile(
    db_path: Path,
    scoring_config: "ScoringConfig",
    column: str,
) -> int:
    """Compute the per-profile score for every parcel and write to `column`."""
    # Re-use the existing per-parcel scoring logic, but write to `column`
    # instead of the hard-coded 'score' column.
    conn = get_connection(db_path)
    try:
        # SELECT columns we need = union of all signal columns from the config.
        signal_cols = [s.signal for s in scoring_config.signals]
        select_cols = ", ".join(["pin"] + signal_cols)
        rows = conn.execute(f"SELECT {select_cols} FROM parcels").fetchall()
        n = 0
        for r in rows:
            parcel_dict = dict(r)
            pin = parcel_dict["pin"]
            score_value = score_parcel(parcel_dict, scoring_config)
            conn.execute(
                f"UPDATE parcels SET {column} = ? WHERE pin = ?",
                (score_value, pin),
            )
            n += 1
        conn.commit()
        return n
    finally:
        conn.close()
```

Note: `score_parcel` (singular) is the existing pure-function scorer. If its signature differs, adapt the call site to match.

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_score.py::test_score_parcels_multi_profile_writes_separate_columns -v`

Expected: PASS.

- [ ] **Step 6: Run all score tests to confirm no regression**

Run: `python -m pytest tests/test_score.py tests/test_pipeline_enrichment.py -v`

Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add pipeline/score.py tests/test_score.py
git commit -m "feat(score): score_parcels_multi for multi-profile output

New entry point: takes a list of (profile_name, ScoringConfig,
score_column) tuples and writes each profile's computed score to its
named column. Existing score_parcels (single-profile, writes 'score'
column) preserved for back-compat.

Engine still does NOT filter parcels — every parcel gets every score.
Filtering happens in the UI per the spec.

Phase 4 of the scoring-profiles spec."
```

## Task 4.3: Create `config/profile_defaults.yaml`

**Files:**
- Create: `config/profile_defaults.yaml`
- Create: `tests/test_profile_defaults_loader.py`
- Modify: (new) `pipeline/profile_defaults.py` — the loader module

- [ ] **Step 1: Write the failing loader test**

Create `tests/test_profile_defaults_loader.py`:

```python
import pytest
from pathlib import Path


def test_load_profile_defaults_returns_registered_profiles(tmp_path):
    """load_profile_defaults returns a dict keyed by profile name with
    yaml_path + score_column + recommended_filters per profile."""
    from pipeline.profile_defaults import load_profile_defaults

    cfg = tmp_path / "profile_defaults.yaml"
    cfg.write_text("""\
value_add:
  yaml: config/scoring.yaml
  score_column: score
  recommended_filters: {}

adu:
  yaml: config/scoring_adu.yaml
  score_column: score_adu
  recommended_filters:
    adu_eligible: 1
    lot_size_sf: {between: [3500, 12000]}
""")

    out = load_profile_defaults(cfg)
    assert set(out.keys()) == {"value_add", "adu"}
    assert out["adu"]["yaml"] == "config/scoring_adu.yaml"
    assert out["adu"]["score_column"] == "score_adu"
    assert out["adu"]["recommended_filters"]["adu_eligible"] == 1
    assert out["adu"]["recommended_filters"]["lot_size_sf"] == {"between": [3500, 12000]}


def test_load_profile_defaults_raises_on_missing_required_fields(tmp_path):
    """A profile entry missing 'yaml' or 'score_column' raises with a
    clear message naming the profile."""
    from pipeline.profile_defaults import load_profile_defaults

    cfg = tmp_path / "bad.yaml"
    cfg.write_text("adu:\n  recommended_filters: {}\n")  # no yaml, no score_column
    with pytest.raises(KeyError, match="adu"):
        load_profile_defaults(cfg)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_profile_defaults_loader.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'pipeline.profile_defaults'`.

- [ ] **Step 3: Implement the loader**

Create `pipeline/profile_defaults.py`:

```python
"""Profile defaults registry — binds profile name → scoring YAML →
score column → recommended UI filters.

Source of truth: config/profile_defaults.yaml. Loaded by:
  - pipeline/fetch_all.py (to know which YAMLs to score)
  - webapp routes (to serve recommended filters via /api/profile-defaults)
"""
from __future__ import annotations
from pathlib import Path
from typing import Any

import yaml


REQUIRED_FIELDS = ("yaml", "score_column")


def load_profile_defaults(path: Path) -> dict[str, dict[str, Any]]:
    """Load the registry. Returns dict keyed by profile_name with:
      - yaml: relative path to the scoring YAML
      - score_column: column in parcels to write
      - recommended_filters: dict of filter defaults (auto-applied in UI)

    Raises KeyError if a profile entry is missing required fields.
    """
    with Path(path).open() as f:
        raw = yaml.safe_load(f) or {}
    for profile_name, body in raw.items():
        for field in REQUIRED_FIELDS:
            if field not in body:
                raise KeyError(
                    f"profile_defaults.yaml: profile {profile_name!r} "
                    f"missing required field {field!r}"
                )
        body.setdefault("recommended_filters", {})
    return raw
```

- [ ] **Step 4: Create the actual `config/profile_defaults.yaml`**

```yaml
# Registry of scoring profiles + per-profile recommended UI filters.
# - `yaml`: path to the scoring YAML (relative to project root)
# - `score_column`: parcels column the profile's score is written to
# - `recommended_filters`: filter defaults auto-applied when the profile
#   is selected in the UI dropdown (non-destructive — user-set filters win).

value_add:
  yaml: config/scoring.yaml
  score_column: score
  recommended_filters: {}

adu:
  yaml: config/scoring_adu.yaml
  score_column: score_adu
  recommended_filters:
    adu_eligible: 1
    is_condo_unit: 0
    lot_size_sf: {between: [3500, 12000]}
    lot_width_ft: {not_null: true}

redev:
  yaml: config/scoring_redev.yaml
  score_column: score_redev
  recommended_filters:
    is_condo_unit: 0
    lot_size_sf: {min: 5000}
    zone_class: {prefix_in: ["RT-", "RM-", "B", "C"]}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_profile_defaults_loader.py -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add config/profile_defaults.yaml pipeline/profile_defaults.py tests/test_profile_defaults_loader.py
git commit -m "feat(scoring): profile_defaults.yaml registry + loader

Binds profile_name → scoring YAML → parcels score column → recommended
UI filter defaults. Three profiles registered: value_add (existing),
adu, redev. Recommended filters are auto-applied in the UI when the
operator picks a profile (non-destructive).

The two new scoring YAMLs (scoring_adu.yaml, scoring_redev.yaml) are
added in the next task, after Hunter approves the proposed weights."
```

## Task 4.4: Add the two new scoring YAMLs — **GATED ON HUNTER'S APPROVAL**

**Files:**
- Create: `config/scoring_adu.yaml`
- Create: `config/scoring_redev.yaml`

**This task ships the actual signal weights.** Per the spec's standing instruction ("Don't change the scores without confirming first"), this task must NOT be merged until Hunter has reviewed the proposed YAMLs.

- [ ] **Step 1: Surface the proposed YAMLs for Hunter's review**

Before writing the files, print them so Hunter can see exactly what will go in:

```bash
cat <<'EOF'
=== Proposed config/scoring_adu.yaml ===
version: 1.0.0-2026-06-08
top_n: 20
signals:
  lot_width_ft:
    weight: 0.30
    kind: continuous
    direction: positive
    insignificant: false
    normalization: {min: 25, max: 50}
  lot_size_sf:
    weight: 0.15
    kind: continuous
    direction: positive
    insignificant: false
    normalization: {min: 3500, max: 12000}
  adu_eligible:
    weight: 0.20
    kind: binary
    direction: positive
    insignificant: false
    normalization: {min: 0, max: 1}
  adu_has_annual_limits:
    weight: 0.05
    kind: binary
    direction: negative
    insignificant: false
    normalization: {min: 0, max: 1}
  last_sale_price_recent:
    weight: 0.10
    kind: continuous
    direction: negative
    insignificant: false
    normalization: {min: 200000, max: 1500000}
  hold_duration_years:
    weight: 0.10
    kind: continuous
    direction: positive
    insignificant: false
    normalization: {min: 5, max: 30}
  years_since_last_permit:
    weight: 0.10
    kind: continuous
    direction: positive
    insignificant: false
    normalization: {min: 3, max: 25}
EOF
```

Pause for Hunter's explicit "approved" before proceeding.

- [ ] **Step 2: Create `config/scoring_adu.yaml`**

Once approved, write the file with the contents from Step 1 (no surprises — same content the operator approved).

- [ ] **Step 3: Create `config/scoring_redev.yaml`**

```yaml
version: 1.0.0-2026-06-08
top_n: 20
signals:
  far_gap_delta:
    weight: 0.30
    kind: continuous
    direction: positive
    insignificant: false
    normalization: {min: 0.5, max: 2.5}
  is_low_util_land:
    weight: 0.20
    kind: binary
    direction: positive
    insignificant: false
    normalization: {min: 0, max: 1}
  lot_size_sf:
    weight: 0.15
    kind: continuous
    direction: positive
    insignificant: false
    normalization: {min: 1000, max: 50000}
  max_far:
    weight: 0.10
    kind: continuous
    direction: positive
    insignificant: false
    normalization: {min: 1.0, max: 7.0}
  allows_multifamily_by_right:
    weight: 0.10
    kind: binary
    direction: positive
    insignificant: false
    normalization: {min: 0, max: 1}
  cta_distance_ft:
    weight: 0.15
    kind: continuous
    direction: negative
    insignificant: false
    normalization: {min: 0, max: 4000}
```

- [ ] **Step 4: Verify both YAMLs load cleanly**

```bash
python -c "
from pipeline.score import load_scoring_config
from pathlib import Path
for p in ['config/scoring_adu.yaml', 'config/scoring_redev.yaml']:
    cfg = load_scoring_config(Path(p))
    total = sum(s.weight for s in cfg.signals)
    print(f'{p}: {len(cfg.signals)} signals, weights sum to {total:.4f}')
    assert abs(total - 1.0) < 0.001, f'{p}: weights must sum to 1.0'
"
```

Expected: both files load and sum to 1.0.

- [ ] **Step 5: Commit**

```bash
git add config/scoring_adu.yaml config/scoring_redev.yaml
git commit -m "feat(scoring): ship ADU + redev profile YAMLs

Per-profile signal weights approved by Hunter on 2026-06-08.

ADU profile prioritizes lot width (0.30) + ADU eligibility (0.20) +
lot size band (0.15); penalizes recent high purchase price + annual-
limit RS zones; rewards long-held + deferred-permit parcels (motivated
seller proxies).

Redev profile prioritizes far_gap_delta (0.30 — the 'underzoned'
signal) + low-utilization land (0.20) + lot size (0.15); rewards
high max_far + multifamily-by-right zoning + CTA proximity.

Both sum to 1.00. Phase 4 of the scoring-profiles spec."
```

## Task 4.5: Wire `fetch_all` to score all registered profiles

**Files:**
- Modify: `pipeline/fetch_all.py`

- [ ] **Step 1: Locate the existing score invocation**

Run: `grep -n "score_parcels\|from pipeline.score\|from pipeline import score" pipeline/fetch_all.py`

Identify where the current single-profile `score_parcels` is called.

- [ ] **Step 2: Replace with multi-profile invocation**

Wherever the current single-profile call lives, replace with:

```python
from pipeline.profile_defaults import load_profile_defaults
from pipeline.score import (
    derive_last_sale_price_recent,
    load_scoring_config,
    score_parcels_multi,
)

# Derived columns the scoring engine reads.
derive_last_sale_price_recent(db_path)

# Build the multi-profile config list from the registry.
profiles = load_profile_defaults(Path("config/profile_defaults.yaml"))
profile_configs = [
    (name, load_scoring_config(Path(body["yaml"])), body["score_column"])
    for name, body in profiles.items()
]
results.append(_run("score (all profiles)",
                    score_parcels_multi, db_path, db_path, profile_configs))
```

(Adapt the `_run` wrapper signature to whatever pattern `fetch_all.py` uses for other source calls.)

- [ ] **Step 3: Run the full test suite**

Run: `python -m pytest -q`

Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add pipeline/fetch_all.py
git commit -m "feat(fetch): score all registered profiles via profile_defaults

fetch_all now reads config/profile_defaults.yaml, derives
last_sale_price_recent, then runs score_parcels_multi across every
registered profile in one pass. Adds two new score columns (score_adu,
score_redev) on every refresh.

Phase 4 wiring complete; UI dropdown lands in Phase 5."
```

## Task 4.6: Add new filterable columns to `config/ui_filters.yaml`

**Files:**
- Modify: `config/ui_filters.yaml`

- [ ] **Step 1: Add filter entries for the new columns**

In `config/ui_filters.yaml`, locate the existing groups (e.g. "Score", "Location", "Owner", etc.). Add a new group OR extend an existing one. Following the existing pattern:

```yaml
  - group: ADU
    filters:
      - column: adu_eligible
        label: ADU eligible
        type: checkbox
      - column: adu_has_annual_limits
        label: ADU annual limits apply
        type: checkbox
      - column: adu_restriction_text
        label: ADU restriction
        type: dropdown
      - column: lot_width_ft
        label: Lot width (ft)
        type: range
      - column: lot_depth_ft
        label: Lot depth (ft)
        type: range
```

- [ ] **Step 2: Verify the schema enriches correctly against the DB**

```bash
python -c "
from pathlib import Path
from webapp.filter_schema import build_filter_schema
out = build_filter_schema(Path('data/full.alt.db'), Path('config/ui_filters.yaml'))
adu = [g for g in out['filter_groups'] if g['group']=='ADU'][0]
print('ADU group filters:')
for f in adu['filters']:
    print(f'  {f}')
"
```

Expected: 5 filters enriched with their type-specific metadata (min/max for ranges, options for dropdown, etc.).

- [ ] **Step 3: Commit**

```bash
git add config/ui_filters.yaml
git commit -m "feat(ui): register ADU + lot-width filterable columns

New ADU group surfaces the per-parcel eligibility + restriction +
lot geometry filters in the UI. Consumed by /api/parcels and the
profile_defaults auto-apply flow (Phase 5)."
```

---

# Phase 5: UI profile dropdown + auto-apply filter merge

Frontend changes: dropdown markup, `/api/profile-defaults` route, `/api/parcels` `?profile=` support, JS handler.

## Task 5.1: Add `/api/profile-defaults` route

**Files:**
- Modify: `webapp/routes.py`
- Modify: `webapp/app.py` — load `profile_defaults.yaml` at startup
- Test: `tests/test_webapp_routes.py` (or wherever similar route tests live)

- [ ] **Step 1: Locate webapp config + route patterns**

Run:

```bash
grep -n "PROFILE_DEFAULTS\|OUTREACH_TEMPLATES_PATH\|app.config\[" webapp/app.py | head -10
grep -n "@app.get\|def api_" webapp/routes.py | head -10
```

Familiarize yourself with the existing config-loading pattern (e.g. `OUTREACH_TEMPLATES_PATH`) and route registration style.

- [ ] **Step 2: Write the failing test**

Add to the relevant test file (probably `tests/test_webapp_routes.py` or a new `tests/test_webapp_profile_defaults.py`):

```python
def test_api_profile_defaults_returns_registry(tmp_path):
    """GET /api/profile-defaults returns the loaded registry as JSON."""
    from webapp.app import create_app
    from pipeline.db import init_db

    db = tmp_path / "t.db"
    init_db(db)

    cfg = tmp_path / "profile_defaults.yaml"
    cfg.write_text("""\
adu:
  yaml: config/scoring_adu.yaml
  score_column: score_adu
  recommended_filters:
    adu_eligible: 1
    lot_size_sf: {between: [3500, 12000]}
""")

    app = create_app(db_path=db, feature_outreach=False,
                     profile_defaults_path=cfg)
    resp = app.test_client().get("/api/profile-defaults")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "adu" in data
    assert data["adu"]["score_column"] == "score_adu"
    assert data["adu"]["recommended_filters"]["adu_eligible"] == 1
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_webapp_routes.py::test_api_profile_defaults_returns_registry -v`

Expected: FAIL with `TypeError` (unknown `profile_defaults_path` kwarg) or `404` on the route.

- [ ] **Step 4: Wire the config path into `create_app`**

In `webapp/app.py`, add a `profile_defaults_path` parameter to `create_app`:

```python
def create_app(
    db_path: Path,
    feature_outreach: bool = False,
    ...
    profile_defaults_path: Path | None = None,
) -> Flask:
    ...
    app.config["PROFILE_DEFAULTS_PATH"] = profile_defaults_path or (
        Path(__file__).resolve().parent.parent / "config" / "profile_defaults.yaml"
    )
```

- [ ] **Step 5: Add the route**

In `webapp/routes.py`, add (in the `register(app)` function, alongside other top-level routes):

```python
@app.get("/api/profile-defaults")
def api_profile_defaults():
    """Return the profile registry: {profile_name: {score_column, recommended_filters}}.
    The UI uses this to populate the profile dropdown + auto-apply
    recommended filters when the operator picks a profile."""
    from pipeline.profile_defaults import load_profile_defaults
    out = load_profile_defaults(Path(app.config["PROFILE_DEFAULTS_PATH"]))
    return jsonify(out)
```

- [ ] **Step 6: Run test to verify it passes**

Run: `python -m pytest tests/test_webapp_routes.py::test_api_profile_defaults_returns_registry -v`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add webapp/app.py webapp/routes.py tests/test_webapp_routes.py
git commit -m "feat(webapp): /api/profile-defaults route

Returns the profile registry (score_column + recommended_filters per
profile). Consumed by the UI profile dropdown for auto-apply filter
merge on profile change."
```

## Task 5.2: Add `?profile=` query param support to `/api/parcels`

**Files:**
- Modify: `webapp/routes.py` (the `api_parcels` route)
- Test: `tests/test_webapp_routes.py`

- [ ] **Step 1: Write the failing test (fixture inline so the test is self-contained)**

```python
import sqlite3
import pytest


@pytest.fixture
def app_with_two_profile_scores(tmp_path):
    """App seeded with 3 parcels carrying score + score_adu values plus a
    profile_defaults.yaml registering value_add and adu."""
    from pipeline.db import init_db
    from webapp.app import create_app

    db = tmp_path / "t.db"
    init_db(db)
    with sqlite3.connect(db) as conn:
        conn.executemany(
            "INSERT INTO parcels(pin, pin10, address, score, score_adu) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                ("00000000000001", "0000000000", "100 A St", 90.0, 30.0),
                ("00000000000002", "0000000000", "200 B St", 50.0, 80.0),
                ("00000000000003", "0000000000", "300 C St", 70.0, 60.0),
            ],
        )
        conn.commit()

    cfg = tmp_path / "profile_defaults.yaml"
    cfg.write_text("""\
value_add:
  yaml: config/scoring.yaml
  score_column: score
  recommended_filters: {}

adu:
  yaml: config/scoring_adu.yaml
  score_column: score_adu
  recommended_filters: {}
""")

    return create_app(
        db_path=db, feature_outreach=False,
        profile_defaults_path=cfg,
    )


def test_api_parcels_orders_by_profile_score_column(app_with_two_profile_scores):
    """When ?profile=adu, results are sorted by score_adu DESC instead
    of the default `score` column."""
    client = app_with_two_profile_scores.test_client()
    resp = client.get("/api/parcels?profile=adu&limit=5")
    assert resp.status_code == 200
    data = resp.get_json()
    # Sort assertion: descending score_adu. The seeded parcels (90/30,
    # 50/80, 70/60) should order as B(80), C(60), A(30).
    scores = [row["score_adu"] for row in data["parcels"]
              if row.get("score_adu") is not None]
    assert scores == sorted(scores, reverse=True)
    assert scores[0] == 80.0  # pin 00000000000002 (B St)


def test_api_parcels_rejects_unknown_profile(app_with_two_profile_scores):
    """An unknown profile param returns 400."""
    client = app_with_two_profile_scores.test_client()
    resp = client.get("/api/parcels?profile=nonexistent")
    assert resp.status_code == 400


def test_api_parcels_defaults_to_legacy_score_column(app_with_two_profile_scores):
    """Without ?profile, behavior unchanged — sorted by `score` DESC.
    Seeded parcels (90/30, 50/80, 70/60) order as A(90), C(70), B(50)."""
    client = app_with_two_profile_scores.test_client()
    resp = client.get("/api/parcels?limit=5")
    assert resp.status_code == 200
    data = resp.get_json()
    scores = [row["score"] for row in data["parcels"] if row.get("score") is not None]
    assert scores == sorted(scores, reverse=True)
    assert scores[0] == 90.0  # pin 00000000000001 (A St)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_webapp_routes.py -k "profile" -v`

Expected: FAIL — unknown query param ignored, ordering by `score` regardless.

- [ ] **Step 3: Implement profile-aware ordering in `api_parcels`**

In `webapp/routes.py`, inside `api_parcels`, find the existing `ORDER BY score DESC` clause. Wrap it with profile resolution:

```python
# Profile selection: ?profile=<name> picks which score column to sort by.
# Resolves via profile_defaults.yaml registry; unknown profile → 400.
profile_param = request.args.get("profile")
if profile_param:
    from pipeline.profile_defaults import load_profile_defaults
    profiles = load_profile_defaults(Path(app.config["PROFILE_DEFAULTS_PATH"]))
    if profile_param not in profiles:
        abort(400, f"unknown profile: {profile_param}")
    score_column = profiles[profile_param]["score_column"]
else:
    score_column = "score"

# ... later in the query construction ...
order_by_clause = f"ORDER BY {score_column} DESC NULLS LAST"
```

Apply `order_by_clause` instead of the hard-coded `ORDER BY score DESC`. Also include the profile's score column in the SELECT list so the response carries it.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_webapp_routes.py -k "profile" -v`

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add webapp/routes.py tests/test_webapp_routes.py
git commit -m "feat(webapp): /api/parcels accepts ?profile= for sort column

Resolves the profile name via profile_defaults.yaml → score column.
Unknown profile returns 400. Omitted profile defaults to the legacy
'score' column (value-add behavior unchanged). Score column is
included in the SELECT so the response carries it for the UI."
```

## Task 5.3: Add the profile dropdown to the UI

**Files:**
- Modify: `webapp/templates/index.html` (top bar area)
- Modify: `webapp/static/js/app.js` (or wherever the parcels list controller is — locate via `grep`)
- Modify: `webapp/static/css/style.css` (minor styling)

- [ ] **Step 1: Locate the existing top-bar HTML**

Run: `grep -n "top-bar\|score-version\|top-bar-meta" webapp/templates/index.html`

Identify where to inject the dropdown.

- [ ] **Step 2: Add the dropdown markup**

In `webapp/templates/index.html`, inside the top-bar (next to the existing score-version/top-N meta), add:

```html
<select id="profile-selector" class="profile-selector">
  <!-- options populated by JS from /api/profile-defaults -->
</select>
```

- [ ] **Step 3: Add minimal styling**

In `webapp/static/css/style.css`:

```css
.profile-selector {
  margin-left: 12px;
  padding: 4px 8px;
  background: #21262d;
  color: #c9d1d9;
  border: 1px solid #30363d;
  border-radius: 4px;
  font-size: 13px;
}
```

- [ ] **Step 4: Locate the parcels list controller**

Run: `grep -rn "api/parcels\|fetchParcels\|loadParcels" webapp/static/js/ | head -10`

The list controller is whichever JS file owns the fetch + render of the parcels list. Likely `webapp/static/js/list.js` or `app.js`.

- [ ] **Step 5: Implement the dropdown handler with auto-apply filter merge**

In the list controller's initialization block (after DOMContentLoaded), add:

```javascript
async function initProfileSelector() {
  const sel = document.getElementById('profile-selector');
  if (!sel) return;

  // Fetch the profile registry once.
  let registry;
  try {
    const resp = await fetch('/api/profile-defaults');
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    registry = await resp.json();
  } catch (e) {
    console.warn('profile-defaults fetch failed:', e);
    return;
  }

  // Populate options. Order: value_add first (default), then the rest
  // in object-insertion order.
  const profileLabels = {
    value_add: 'Value-add multifamily',
    adu: 'ADU candidates',
    redev: 'Redevelopment',
  };
  Object.keys(registry).forEach(name => {
    const opt = document.createElement('option');
    opt.value = name;
    opt.textContent = profileLabels[name] || name;
    sel.appendChild(opt);
  });

  // Restore last-selected profile from localStorage.
  const saved = localStorage.getItem('selectedProfile') || 'value_add';
  if (registry[saved]) {
    sel.value = saved;
  }

  sel.addEventListener('change', () => {
    const profileName = sel.value;
    localStorage.setItem('selectedProfile', profileName);
    // Auto-apply recommended filters (non-destructive — only fill
    // filter slots the user hasn't already set).
    const defaults = registry[profileName].recommended_filters || {};
    mergeFiltersIntoUrl(defaults);
    // Trigger a fresh parcels-list fetch via the existing URL-state
    // change mechanism. If the list listens for popstate, dispatch one;
    // otherwise call the fetcher directly.
    window.dispatchEvent(new Event('profilechanged'));
  });
}

function mergeFiltersIntoUrl(defaults) {
  // Merge defaults into the URL query string only for keys not already
  // present. Updates URL via history.replaceState so back-button works.
  const url = new URL(window.location.href);
  const params = url.searchParams;
  for (const [key, val] of Object.entries(defaults)) {
    if (params.has(`filter_${key}`)) continue;  // user-set wins
    // Encode nested values (e.g. {between: [3500, 12000]}) as JSON.
    const encoded = typeof val === 'object' ? JSON.stringify(val) : String(val);
    params.set(`filter_${key}`, encoded);
  }
  // Also set the profile param so the API sorts by score_<profile>.
  params.set('profile', document.getElementById('profile-selector').value);
  window.history.replaceState({}, '', url.toString());
}

initProfileSelector();
```

(Adapt the filter URL key prefix (`filter_<key>`) to match whatever scheme the existing UI uses — find via `grep -n "filter_\|params.set\|searchParams" webapp/static/js/*.js`.)

Also: have the existing parcels-list fetcher listen for the `profilechanged` event to trigger a re-fetch. Or, simpler, call the existing fetcher directly from the change handler.

- [ ] **Step 6: Manual smoke check**

```bash
# Restart the webapp
kill $(lsof -tiTCP:5051 -sTCP:LISTEN 2>/dev/null) 2>/dev/null; sleep 1
source .venv/bin/activate && python -m webapp --db data/full.alt.db --outreach --port 5051 &
# Wait for ready
until curl -s -o /dev/null -w "%{http_code}" http://localhost:5051/ | grep -q "200"; do sleep 0.3; done
echo "ready"
```

Open http://localhost:5051/ in browser (hard-refresh). Confirm:
1. Dropdown appears in the top bar
2. Selecting "ADU candidates" reloads the URL with `profile=adu&filter_adu_eligible=1&filter_lot_size_sf=...`
3. The parcels list re-sorts (top entries score high on ADU)
4. Selection persists across page reload

- [ ] **Step 7: Commit**

```bash
git add webapp/templates/index.html webapp/static/js/ webapp/static/css/style.css
git commit -m "feat(ui): profile dropdown + auto-apply recommended filters

Top-bar dropdown populated from /api/profile-defaults. Selecting a
profile:
  1. Persists choice to localStorage
  2. Merges the profile's recommended_filters into the URL (non-
     destructive — user-set filters preserved)
  3. Adds ?profile=<name> so /api/parcels sorts by score_<profile>

Default profile: value_add (backward-compatible with current UX).
Phase 5 complete."
```

---

# Phase 6: End-to-end smoke run

Hunter validates the integrated pipeline against `data/full.alt.db`.

## Task 6.1: Refresh pipeline → score → sanity-check top-20s

**Manual task.** Not part of CI.

- [ ] **Step 1: Run the full pipeline**

```bash
cd /Users/hunterheyman/Claude/chicago-pipeline
source .venv/bin/activate
python -m pipeline.fetch_all --db data/full.alt.db
```

Expected: completes in ~30 min. Final lines list each source's row count, including `score (all profiles): N` (where N = total parcels scored).

- [ ] **Step 2: Confirm score column population**

```bash
python -c "
import sqlite3
con = sqlite3.connect('data/full.alt.db')
for col in ('score', 'score_adu', 'score_redev'):
    n = con.execute(f'SELECT COUNT(*) FROM parcels WHERE {col} IS NOT NULL').fetchone()[0]
    print(f'{col}: {n} parcels scored')
"
```

Expected: all three columns populated for the same number of parcels (typically ~60k+).

- [ ] **Step 3: Sanity-check the top-20 per profile**

```bash
python -c "
import sqlite3
con = sqlite3.connect('data/full.alt.db')
con.row_factory = sqlite3.Row
for profile, col in [('value_add', 'score'), ('adu', 'score_adu'), ('redev', 'score_redev')]:
    print(f'=== Top 5 by {profile} ({col}) ===')
    for r in con.execute(f'SELECT pin, address, zone_class, lot_size_sf, lot_width_ft, {col} AS s FROM parcels WHERE {col} IS NOT NULL ORDER BY {col} DESC LIMIT 5'):
        print(f'  pin={r[\"pin\"]}  addr={r[\"address\"]}  zone={r[\"zone_class\"]}  lot={r[\"lot_size_sf\"]:.0f}sf  width={r[\"lot_width_ft\"]:.0f}ft  score={r[\"s\"]:.3f}')
    print()
"
```

Expected:
- `value_add` top-5: similar to the previous canonical list (multifamily-leaning teardown candidates)
- `adu` top-5: residential parcels with wide lots (lot_width_ft > 35), all `adu_eligible=1`
- `redev` top-5: larger lots in RT/RM/B/C zones with high `far_gap_delta`

Spot-check 2–3 parcels per profile against your real-estate intuition. If a top entry looks obviously wrong (e.g. an ADU top hit is in a manufacturing zone), that's a signal to revisit the YAML weights.

- [ ] **Step 4: Test the live UI**

Restart the webapp (if not running) and switch between profiles in the dropdown. Confirm:
- Each profile's top-20 list is different
- Auto-applied filters appear in the filter bar and can be toggled off
- Map markers re-rank (if the map shows score-based markers)

**Gate before declaring done:** Hunter confirms the top-20 lists look right BEFORE any outreach is initiated against them.

- [ ] **Step 5: Memory + spec status update**

Update the spec's status header from "Draft" to "Implemented":

```bash
sed -i '' 's/^\*\*Status:\*\* Draft, 2026-06-08/\*\*Status:\*\* Implemented, 2026-06-08/' docs/superpowers/specs/2026-06-08-scoring-adu-redev-design.md
git add docs/superpowers/specs/2026-06-08-scoring-adu-redev-design.md
git commit -m "docs(spec): mark scoring-adu-redev as implemented"
```

---

## Risks & open items left as-is per the spec

- **AVM / Zillow integration** explicitly out of scope (per spec's Out of Scope section). The `last_sale_price_recent` column gives an approximation when the parcel sold recently; missing data falls through to the engine's neutral 0.5 normalization.
- **Lot width approximation for irregular parcels** — `minimum_rotated_rectangle` over-states width on L-shaped lots by up to ~10%. Acceptable; the spec accepts this trade-off explicitly. If it becomes a real ranking problem in practice, future work can add a "shape regularity" metric (polygon area : MBR area ratio).
- **ADU map staleness** — Hunter manually refreshes via re-running the source (`python -c "from sources.chicago_adu_zones import fetch, apply_to_parcels; fetch(...); apply_to_parcels(...)"`). Already covered by Phase 3 Task 3.6.
