# Chicago Pipeline — Score/Analyze Fixes + Consolidation-Group Scoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix four issues found during the post-Score bug check:
1. Score is processing 44,547 individual condo unit PINs that Analyze had explicitly excluded — they dominate the top-N with bogus scores. Stop scoring them.
2. Consolidation groups (same-owner adjacent parcel clusters — the actual redevelopment opportunities) are not scored at all. Score them.
3. The Analyze training set excludes condos and consolidation groups, so the lot-size normalization range `[3180, 8369]` reflects only standard small-residential parcels. Re-train Analyze with consolidation groups added so the normalization captures the redevelopment-relevant scale.
4. The substitute-YAML workflow already works via `--scoring-yaml` — document it.

**Scoring entities after this plan:**
- ✅ Single non-condo parcels (`is_condo_unit=0` AND `is_condo_building=0`) — unchanged.
- ✅ Condo building reps (`is_condo_unit=0` AND `is_condo_building=1`) — unchanged. The building rep PIN is the right entity to score because the unit owners can't sell the building, but the building can be acquired in aggregate.
- ✅ Consolidation groups (rows in `consolidation_groups` table) — **new**. Aggregated features per group, scored into a new `consolidation_groups.score` column.
- ❌ Individual condo units (`is_condo_unit=1`) — explicitly skipped at score time, leaving their `score` column NULL.

**Architecture:**
- New shared module `pipeline/consolidation_features.py` with `derive_group_features(group_id, db_path) -> dict` that aggregates a group's constituent-parcel signals using documented per-signal rules. Reused by both Analyze (training) and Score.
- `pipeline/analyze.py` — `build_training_table` extended to include consolidation groups as additional training rows (one row per group, identifier `pin = f"group:{id}"` so the existing DataFrame schema doesn't change).
- `pipeline/score.py` — `score_parcels` filters out `is_condo_unit=1` rows; new `score_consolidation_groups` function. Schema migration adds `score`/`score_version` columns to `consolidation_groups`.
- Re-run Analyze (new YAML, version bumped to `1.1.0-YYYY-MM-DD`) and Score against `data/full.db`.
- New `chicago-pipeline/README.md` section documenting the substitute-YAML workflow.

**Tech stack:** unchanged — Python 3.14, SQLite, pandas, scikit-learn, pyyaml.

**Verification baseline:** before starting, `.venv/bin/pytest -q` must show **205 passing**. After every task, all tests must still pass. Final run produces an updated `config/scoring.yaml`, an updated `docs/analysis/2026-04-28-initial-scoring-weights.md`, populated `parcels.score` (filtered to non-condo-units), populated `consolidation_groups.score`, and a README section.

---

## Per-signal aggregation rules (locked in upfront)

Spec for `derive_group_features(group_id, db_path)`. Each signal is computed from the group's constituent parcel rows.

| Signal | Rule | Notes |
|---|---|---|
| `lot_size_sf` | `combined_lot_size_sf` from group | Already on the table. |
| `building_sf` | `combined_building_sf` from group | Already on the table. |
| `hold_duration_years` | **MIN** across constituents | Most-recent acquisition in the group — the group's effective hold. |
| `estimated_annual_tax` | **SUM** | Combined annual holding cost. |
| `tax_increase_pct_5yr` | **AVG weighted by `assessed_total`** | Weighted because a parcel with 100x the assessment shouldn't be averaged with one that has 0.01x. |
| `cta_distance_ft` | **MIN** | Closest constituent — group inherits its best transit access. |
| `appeal_count` | **SUM** | Total appeals filed across the group. |
| `open_violations_count` | **SUM** | |
| `years_since_last_permit` | **MIN** | Most-recent permit in the group. |
| `vacant_violations_count` | **SUM** | |
| `scofflaw_appearances_count` | **SUM** | |
| `is_absentee` | **MAX** (any → 1) | If any constituent is absentee, the group is. |
| `is_llc` | **MAX** | Shared by definition (consolidation groups by owner) but defensive MAX in case of mixed shells. |
| `is_scofflaw` | **MAX** | |
| `allows_multifamily_by_right` | **MAX** | Most permissive — if any constituent allows MF, the group does post-zoning-merge. |
| `max_far` | **MAX** | Most permissive zoning capacity in the group. |
| `far_gap_delta` | **recompute** as `MAX(max_far) − SUM(building_sf) / SUM(lot_size_sf)` | The group's combined built-FAR vs. its best zoning capacity. |
| `land_building_ratio` | **recompute** as `SUM(assessed_land) / SUM(assessed_total)` | Group-level redevelopment-opportunity ratio. |

Helper signal columns we need to query but don't directly score: `assessed_total`, `assessed_land`. Used only for the two recomputed signals.

When all constituents are NULL for a SUM signal, return None. When all constituents are NULL for a MIN/MAX/AVG signal, return None. Score's `normalize_signal` already handles None at score time.

---

## Phase A — Shared aggregation helper

### Task 1: Create `pipeline/consolidation_features.py` with `derive_group_features`

**Files:**
- Create: `pipeline/consolidation_features.py`
- Create: `tests/test_pipeline_consolidation_features.py`

The single helper that both Analyze and Score will call. Pure function: takes a `group_id` and a `db_path`, returns a dict matching the SIGNALS columns plus the keys Score needs.

- [ ] **Step 1: Write the failing test**

Create `tests/test_pipeline_consolidation_features.py`:

```python
"""Tests for pipeline/consolidation_features.py — aggregates a consolidation
group's constituent-parcel signals into a single feature dict that mirrors
the parcels-row shape Score and Analyze consume."""
from datetime import datetime, UTC, date
import json

from pipeline import consolidation_features
from pipeline.db import init_db, upsert_rows, get_connection


def _build_db_with_group(tmp_path, parcels, group_id, group_pins,
                         combined_lot=None, combined_bldg=None,
                         owner_name="TEST OWNER"):
    """Build a synthetic DB with parcels + a single consolidation group."""
    db_path = tmp_path / "agg.db"
    init_db(db_path)
    now = datetime.now(UTC).isoformat(timespec="seconds")
    upsert_rows(db_path, "parcels",
                [{**p, "last_fetched_date": now} for p in parcels],
                key_columns=["pin"])
    conn = get_connection(db_path)
    try:
        conn.execute("""
            INSERT INTO consolidation_groups
              (group_id, pins, combined_lot_size_sf, combined_building_sf,
               owner_name, detected_date)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (group_id, json.dumps(group_pins), combined_lot, combined_bldg,
              owner_name, date.today().isoformat()))
        conn.commit()
    finally:
        conn.close()
    return db_path


def test_derive_group_features_aggregates_per_rule(tmp_path):
    """Two adjacent same-owner parcels with mixed signal values; verify each
    aggregation rule produces the documented value."""
    parcels = [
        {"pin": "PIN_A", "lot_size_sf": 3000.0, "building_sf": 2000.0,
         "hold_duration_years": 5.0, "estimated_annual_tax": 8000.0,
         "tax_increase_pct_5yr": 20.0, "cta_distance_ft": 1000.0,
         "appeal_count": 2, "open_violations_count": 1,
         "years_since_last_permit": 3.0, "vacant_violations_count": 0,
         "scofflaw_appearances_count": 0,
         "is_absentee": 1, "is_llc": 1, "is_scofflaw": 0,
         "allows_multifamily_by_right": 1, "max_far": 2.5,
         "assessed_land": 50000.0, "assessed_total": 100000.0},
        {"pin": "PIN_B", "lot_size_sf": 4000.0, "building_sf": 3000.0,
         "hold_duration_years": 2.0, "estimated_annual_tax": 12000.0,
         "tax_increase_pct_5yr": 10.0, "cta_distance_ft": 1500.0,
         "appeal_count": 1, "open_violations_count": 3,
         "years_since_last_permit": 7.0, "vacant_violations_count": 1,
         "scofflaw_appearances_count": 0,
         "is_absentee": 0, "is_llc": 1, "is_scofflaw": 0,
         "allows_multifamily_by_right": 0, "max_far": 1.5,
         "assessed_land": 80000.0, "assessed_total": 200000.0},
    ]
    # Combined values written by consolidate.py would be lot=7000, bldg=5000.
    db_path = _build_db_with_group(tmp_path, parcels, group_id=1,
                                   group_pins=["PIN_A", "PIN_B"],
                                   combined_lot=7000.0, combined_bldg=5000.0)
    f = consolidation_features.derive_group_features(group_id=1, db_path=db_path)

    # Direct from the group row
    assert f["lot_size_sf"] == 7000.0
    assert f["building_sf"] == 5000.0

    # MIN rule: hold_duration_years, cta_distance_ft, years_since_last_permit
    assert f["hold_duration_years"] == 2.0
    assert f["cta_distance_ft"] == 1000.0
    assert f["years_since_last_permit"] == 3.0

    # SUM rule
    assert f["estimated_annual_tax"] == 20000.0
    assert f["appeal_count"] == 3
    assert f["open_violations_count"] == 4
    assert f["vacant_violations_count"] == 1
    assert f["scofflaw_appearances_count"] == 0

    # Weighted AVG by assessed_total: (20*100000 + 10*200000) / 300000 = 13.333…
    assert round(f["tax_increase_pct_5yr"], 4) == round((20*100000 + 10*200000) / 300000, 4)

    # MAX rule on binary signals
    assert f["is_absentee"] == 1     # PIN_A=1
    assert f["is_llc"] == 1          # both
    assert f["is_scofflaw"] == 0
    assert f["allows_multifamily_by_right"] == 1   # PIN_A=1

    # MAX on numeric: max_far
    assert f["max_far"] == 2.5

    # Recomputed: far_gap_delta = MAX(max_far) - combined_building_sf / combined_lot_size_sf
    # = 2.5 - 5000/7000 = 2.5 - 0.7142857 = 1.7857142
    assert round(f["far_gap_delta"], 4) == round(2.5 - 5000.0/7000.0, 4)

    # Recomputed: land_building_ratio = SUM(assessed_land) / SUM(assessed_total)
    # = (50000 + 80000) / (100000 + 200000) = 130000/300000 = 0.4333…
    assert round(f["land_building_ratio"], 4) == round(130000/300000, 4)


def test_derive_group_features_handles_all_null_signal(tmp_path):
    """Signals where ALL constituents are NULL must yield None — Score's
    normalize_signal handles None via the neutral-imputation path."""
    parcels = [
        {"pin": "PIN_A", "lot_size_sf": 3000.0, "estimated_annual_tax": None},
        {"pin": "PIN_B", "lot_size_sf": 4000.0, "estimated_annual_tax": None},
    ]
    db_path = _build_db_with_group(tmp_path, parcels, group_id=1,
                                   group_pins=["PIN_A", "PIN_B"],
                                   combined_lot=7000.0)
    f = consolidation_features.derive_group_features(group_id=1, db_path=db_path)
    assert f["estimated_annual_tax"] is None


def test_derive_group_features_skips_recomputation_when_assessed_total_is_zero(tmp_path):
    """Defensive: if SUM(assessed_total) == 0, land_building_ratio is None
    (avoid div-by-zero). Same for tax_increase_pct_5yr weighted avg."""
    parcels = [
        {"pin": "PIN_A", "lot_size_sf": 3000.0, "assessed_total": 0.0,
         "assessed_land": 0.0, "tax_increase_pct_5yr": 5.0},
        {"pin": "PIN_B", "lot_size_sf": 4000.0, "assessed_total": 0.0,
         "assessed_land": 0.0, "tax_increase_pct_5yr": 10.0},
    ]
    db_path = _build_db_with_group(tmp_path, parcels, group_id=1,
                                   group_pins=["PIN_A", "PIN_B"],
                                   combined_lot=7000.0)
    f = consolidation_features.derive_group_features(group_id=1, db_path=db_path)
    assert f["land_building_ratio"] is None
    assert f["tax_increase_pct_5yr"] is None


def test_derive_group_features_recomputes_far_gap_when_lot_size_is_present(tmp_path):
    """far_gap_delta needs MAX(max_far), combined_building_sf, combined_lot_size_sf.
    If any is missing, return None."""
    parcels = [
        {"pin": "PIN_A", "lot_size_sf": 3000.0, "building_sf": None, "max_far": 2.5},
        {"pin": "PIN_B", "lot_size_sf": 4000.0, "building_sf": None, "max_far": 1.5},
    ]
    db_path = _build_db_with_group(tmp_path, parcels, group_id=1,
                                   group_pins=["PIN_A", "PIN_B"],
                                   combined_lot=7000.0, combined_bldg=None)
    f = consolidation_features.derive_group_features(group_id=1, db_path=db_path)
    assert f["far_gap_delta"] is None
```

- [ ] **Step 2: Run the failing test**

```bash
.venv/bin/pytest tests/test_pipeline_consolidation_features.py -v
```

Expected: FAIL with `ImportError: cannot import name 'consolidation_features' from 'pipeline'`.

- [ ] **Step 3: Implement the module**

Create `pipeline/consolidation_features.py`:

```python
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
```

- [ ] **Step 4: Run the new tests**

```bash
.venv/bin/pytest tests/test_pipeline_consolidation_features.py -v
```

Expected: 4 tests PASS.

- [ ] **Step 5: Full suite**

```bash
.venv/bin/pytest -q
```

Expected: **209 passing** (was 205, +4 new).

- [ ] **Step 6: Commit**

```bash
cd /Users/hunterheyman/Claude/chicago-pipeline
git add pipeline/consolidation_features.py tests/test_pipeline_consolidation_features.py
git commit -m "feat(consolidation): derive_group_features aggregates constituent signals

Per-signal rules: SUM tax/violations/appeals, MIN hold/cta/permit-recency,
MAX max_far/binary-flags, weighted-avg tax_increase, recomputed far_gap_delta
and land_building_ratio. All-NULL signals collapse to None so Score's
normalize_signal handles them via the neutral-imputation path."
```

---

## Phase B — Update Score

### Task 2: Schema migration — add `score` and `score_version` to consolidation_groups

**Files:**
- Modify: `pipeline/db.py`
- Modify: `tests/test_db.py`

The existing `consolidation_groups` table doesn't have score columns. Add via the `_LATER_COLUMNS` migration mechanism so existing DBs (including `data/full.db`) gain the columns on next `init_db()` call without losing data.

- [ ] **Step 1: Read the current `_LATER_COLUMNS` block**

```bash
grep -n "_LATER_COLUMNS\|combined_building_sf" pipeline/db.py
```

Confirm the existing migration mechanism (already adds `combined_building_sf` to `consolidation_groups`).

- [ ] **Step 2: Write the failing test**

Append to `tests/test_db.py`:

```python
def test_consolidation_groups_has_score_columns(tmp_path):
    """Schema migration: consolidation_groups gets score + score_version
    columns on init_db (so existing data/full.db gains them on next open)."""
    from pipeline.db import init_db, get_connection
    db_path = tmp_path / "schema.db"
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        cols = {row[1] for row in conn.execute(
            "PRAGMA table_info(consolidation_groups)"
        ).fetchall()}
    finally:
        conn.close()
    assert "score" in cols
    assert "score_version" in cols
```

- [ ] **Step 3: Run the failing test**

```bash
.venv/bin/pytest tests/test_db.py::test_consolidation_groups_has_score_columns -v
```

Expected: FAIL — `score` not in cols.

- [ ] **Step 4: Add the columns to both `SCHEMA_SQL` and `_LATER_COLUMNS`**

In `pipeline/db.py`, find the `consolidation_groups` CREATE TABLE block in `SCHEMA_SQL`. Add two lines:

```sql
CREATE TABLE IF NOT EXISTS consolidation_groups (
    group_id INTEGER PRIMARY KEY AUTOINCREMENT,
    pins TEXT NOT NULL,
    combined_lot_size_sf REAL,
    combined_building_sf REAL,
    owner_name TEXT,
    detected_date TEXT,
    score REAL,
    score_version TEXT
);
```

And in `_LATER_COLUMNS`, add to the `consolidation_groups` tuple:

```python
"consolidation_groups": (
    ("combined_building_sf", "REAL"),
    ("score", "REAL"),
    ("score_version", "TEXT"),
),
```

- [ ] **Step 5: Run tests**

```bash
.venv/bin/pytest tests/test_db.py -v
.venv/bin/pytest -q
```

Expected: schema test passes; full suite **210 passing**.

- [ ] **Step 6: Commit**

```bash
git add pipeline/db.py tests/test_db.py
git commit -m "feat(db): add score + score_version columns to consolidation_groups

Schema migration via _LATER_COLUMNS so existing data/full.db picks up the
columns on next init_db() call. Score's score_consolidation_groups (next
task) writes to these."
```

---

### Task 3: Filter individual condo units in `score_parcels`

**Files:**
- Modify: `pipeline/score.py`
- Modify: `tests/test_pipeline_score.py`

Score's eligibility filter needs to match Analyze's: skip `is_condo_unit=1` rows, but keep `is_condo_building=1` (the condo building reps).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_pipeline_score.py`:

```python
def test_score_parcels_skips_individual_condo_units(tmp_path):
    """Individual condo units (is_condo_unit=1) must NOT receive a score —
    they were dropped from training and are not redevelopment opportunities.
    Building reps (is_condo_building=1) MUST receive a score."""
    parcels = [
        {"pin": "REGULAR", "lot_size_sf": 5000.0, "is_condo_unit": 0,
         "is_condo_building": 0},
        {"pin": "BUILDING_REP", "lot_size_sf": 40000.0, "is_condo_unit": 0,
         "is_condo_building": 1},
        {"pin": "UNIT_1", "lot_size_sf": 40000.0, "is_condo_unit": 1,
         "is_condo_building": 0},
        {"pin": "UNIT_2", "lot_size_sf": 40000.0, "is_condo_unit": 1,
         "is_condo_building": 0},
    ]
    db_path = _build_score_db(tmp_path, parcels)
    cfg = score.ScoringConfig(version="1.0.0-test", top_n=20, signals=[
        score.SignalConfig(signal="lot_size_sf", kind="continuous",
                           weight=1.0, direction="positive",
                           normalization_min=0.0, normalization_max=10000.0,
                           insignificant=False),
    ])
    n = score.score_parcels(db_path, cfg)
    # Only the regular parcel and the building rep are scored — 2, not 4.
    assert n == 2

    from pipeline.db import get_connection
    conn = get_connection(db_path)
    try:
        rows = {r["pin"]: r["score"] for r in conn.execute(
            "SELECT pin, score FROM parcels"
        ).fetchall()}
    finally:
        conn.close()

    assert rows["REGULAR"] is not None
    assert rows["BUILDING_REP"] is not None
    assert rows["UNIT_1"] is None
    assert rows["UNIT_2"] is None
```

- [ ] **Step 2: Run the failing test**

```bash
.venv/bin/pytest tests/test_pipeline_score.py::test_score_parcels_skips_individual_condo_units -v
```

Expected: FAIL — currently `n == 4` and all rows have scores.

- [ ] **Step 3: Update `score_parcels` to add the `WHERE` clause**

In `pipeline/score.py`, modify the `select_sql` line in `score_parcels`:

```python
    select_sql = ("SELECT " + ", ".join(select_cols) + " FROM parcels "
                  "WHERE COALESCE(is_condo_unit, 0) = 0")
```

The `COALESCE(is_condo_unit, 0) = 0` keeps rows where `is_condo_unit` is NULL or 0, drops rows where it's 1. Defensive against NULL is_condo_unit values (which exist in older DBs).

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest tests/test_pipeline_score.py -v
.venv/bin/pytest -q
```

Expected: 23 passing in test_pipeline_score.py; **211 passing** total.

- [ ] **Step 5: Commit**

```bash
git add pipeline/score.py tests/test_pipeline_score.py
git commit -m "fix(score): skip individual condo units, keep building reps

Match Analyze's eligibility filter — is_condo_unit=1 rows were dropped
from training, so scoring them produces bogus high scores (44,547 of 67,677
parcels are condo units; they were dominating the top-N). Condo building
reps (is_condo_building=1) stay scored — the building can be acquired in
aggregate even though individual units can't drive redevelopment."
```

---

### Task 4: `score_consolidation_groups(db_path, scoring_config)`

**Files:**
- Modify: `pipeline/score.py`
- Modify: `tests/test_pipeline_score.py`

Reads each consolidation group, derives features via `derive_group_features`, scores via `score_parcel`, writes the result.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pipeline_score.py`:

```python
import json as _json


def test_score_consolidation_groups_writes_score_per_group(tmp_path):
    """A 2-parcel consolidation group is scored using aggregated features.
    The result is written to consolidation_groups.score / .score_version."""
    parcels = [
        {"pin": "PIN_A", "lot_size_sf": 3000.0, "estimated_annual_tax": 8000.0,
         "is_condo_unit": 0},
        {"pin": "PIN_B", "lot_size_sf": 4000.0, "estimated_annual_tax": 12000.0,
         "is_condo_unit": 0},
    ]
    db_path = _build_score_db(tmp_path, parcels)
    # Add a consolidation group manually
    from pipeline.db import get_connection
    conn = get_connection(db_path)
    try:
        conn.execute("""
            INSERT INTO consolidation_groups
              (group_id, pins, combined_lot_size_sf, combined_building_sf,
               owner_name, detected_date)
            VALUES (1, ?, 7000.0, NULL, 'TEST OWNER', '2026-04-28')
        """, (_json.dumps(["PIN_A", "PIN_B"]),))
        conn.commit()
    finally:
        conn.close()

    cfg = score.ScoringConfig(version="1.1.0-test", top_n=20, signals=[
        score.SignalConfig(signal="lot_size_sf", kind="continuous",
                           weight=0.5, direction="positive",
                           normalization_min=0.0, normalization_max=10000.0,
                           insignificant=False),
        score.SignalConfig(signal="estimated_annual_tax", kind="continuous",
                           weight=0.5, direction="negative",
                           normalization_min=0.0, normalization_max=20000.0,
                           insignificant=False),
    ])
    n = score.score_consolidation_groups(db_path, cfg)
    assert n == 1

    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT score, score_version FROM consolidation_groups WHERE group_id = 1"
        ).fetchone()
    finally:
        conn.close()
    # combined_lot_size_sf=7000 / 10000 = 0.7 → contribution 0.7 * 0.5 = 0.35
    # SUM(tax)=20000, normalized = 1.0, direction negative → flipped to 0.0
    #   → contribution 0.0 * 0.5 = 0.0
    # total = 0.35 → score 35.0
    assert row["score"] == 35.0
    assert row["score_version"] == "1.1.0-test"


def test_score_consolidation_groups_handles_empty_table(tmp_path):
    db_path = _build_score_db(tmp_path, [])
    cfg = score.ScoringConfig(version="1.1.0-test", top_n=20, signals=[])
    assert score.score_consolidation_groups(db_path, cfg) == 0
```

- [ ] **Step 2: Run the failing test**

```bash
.venv/bin/pytest tests/test_pipeline_score.py::test_score_consolidation_groups_writes_score_per_group -v
```

Expected: FAIL with `AttributeError: ... 'score_consolidation_groups'`.

- [ ] **Step 3: Implement `score_consolidation_groups`**

In `pipeline/score.py`, add the import at the top:

```python
from pipeline.consolidation_features import derive_group_features
```

Append the new function after `score_parcels`:

```python
def score_consolidation_groups(db_path: Path,
                               scoring_config: ScoringConfig) -> int:
    """Score every consolidation group; UPDATE score + score_version per row.

    Each group's features are aggregated from its constituent parcels via
    derive_group_features, then scored through score_parcel using the same
    weights/normalization as parcels. Returns the count of groups updated.
    """
    if not scoring_config.signals:
        return 0
    conn = get_connection(db_path)
    try:
        group_ids = [r["group_id"] for r in conn.execute(
            "SELECT group_id FROM consolidation_groups"
        ).fetchall()]
    finally:
        conn.close()
    if not group_ids:
        return 0

    updates = []
    for gid in group_ids:
        features = derive_group_features(gid, db_path)
        updates.append({
            "group_id": gid,
            "score": score_parcel(features, scoring_config),
            "score_version": scoring_config.version,
        })

    conn = get_connection(db_path)
    try:
        conn.executemany(
            "UPDATE consolidation_groups SET score = :score, "
            "score_version = :score_version WHERE group_id = :group_id",
            updates,
        )
        conn.commit()
    finally:
        conn.close()
    return len(updates)
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest tests/test_pipeline_score.py -v
.venv/bin/pytest -q
```

Expected: 25 passing in test_pipeline_score.py; **213 passing** total.

- [ ] **Step 5: Commit**

```bash
git add pipeline/score.py tests/test_pipeline_score.py
git commit -m "feat(score): score_consolidation_groups via derive_group_features

Aggregates each group's constituent-parcel signals (per the locked
per-signal rules) and runs them through score_parcel. Writes score +
score_version to consolidation_groups. Same weights and normalization as
parcels — the scoring math is identical, only the feature derivation
differs."
```

---

### Task 5: Wire `score_consolidation_groups` into the orchestrator

**Files:**
- Modify: `pipeline/score.py`
- Modify: `tests/test_pipeline_score.py`

The CLI takes the same args; the orchestrator now calls both score paths.

- [ ] **Step 1: Update the orchestrator test**

Modify `test_score_orchestrator_writes_scores` in `tests/test_pipeline_score.py` (or add a new test) to add a consolidation group and verify both parcels.score AND consolidation_groups.score are populated.

Append a new test:

```python
def test_score_orchestrator_writes_both_parcels_and_groups(tmp_path):
    parcels = [
        {"pin": "PIN_A", "lot_size_sf": 3000.0, "is_condo_unit": 0},
        {"pin": "PIN_B", "lot_size_sf": 4000.0, "is_condo_unit": 0},
    ]
    db_path = _build_score_db(tmp_path, parcels)
    from pipeline.db import get_connection
    conn = get_connection(db_path)
    try:
        conn.execute("""
            INSERT INTO consolidation_groups
              (group_id, pins, combined_lot_size_sf, combined_building_sf,
               owner_name, detected_date)
            VALUES (1, ?, 7000.0, NULL, 'TEST OWNER', '2026-04-28')
        """, (_json.dumps(["PIN_A", "PIN_B"]),))
        conn.commit()
    finally:
        conn.close()

    yaml_path = tmp_path / "scoring.yaml"
    _write_yaml(yaml_path, {
        "version": "1.1.0-test",
        "generated_at": "2026-04-28T12:00:00+00:00",
        "top_n": 20,
        "signals": {
            "lot_size_sf": {"kind": "continuous", "weight": 1.0,
                            "direction": "positive",
                            "normalization": {"min": 0.0, "max": 10000.0},
                            "insignificant": False},
        },
    })
    score.score(db_path=db_path, scoring_yaml_path=yaml_path)

    conn = get_connection(db_path)
    try:
        n_parcels_scored = conn.execute(
            "SELECT COUNT(*) FROM parcels WHERE score IS NOT NULL"
        ).fetchone()[0]
        n_groups_scored = conn.execute(
            "SELECT COUNT(*) FROM consolidation_groups WHERE score IS NOT NULL"
        ).fetchone()[0]
    finally:
        conn.close()
    assert n_parcels_scored == 2
    assert n_groups_scored == 1
```

- [ ] **Step 2: Run the failing test**

```bash
.venv/bin/pytest tests/test_pipeline_score.py::test_score_orchestrator_writes_both_parcels_and_groups -v
```

Expected: FAIL — `n_groups_scored == 0` because the orchestrator doesn't call the new function yet.

- [ ] **Step 3: Update the `score()` orchestrator**

In `pipeline/score.py`, modify `score()`:

```python
def score(db_path: Path, scoring_yaml_path: Path) -> None:
    """Orchestrate: load config + score every parcel + every consolidation group."""
    cfg = load_scoring_config(scoring_yaml_path)
    n_parcels = score_parcels(db_path, cfg)
    n_groups = score_consolidation_groups(db_path, cfg)
    print(f"Scored {n_parcels:,} parcels and {n_groups:,} consolidation groups "
          f"with version {cfg.version}")
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest tests/test_pipeline_score.py -v
.venv/bin/pytest -q
```

Expected: 26 passing in test_pipeline_score.py; **214 passing** total.

- [ ] **Step 5: Commit**

```bash
git add pipeline/score.py tests/test_pipeline_score.py
git commit -m "feat(score): orchestrator scores both parcels and consolidation groups

The CLI signature is unchanged (--db, --scoring-yaml). One run now scores
every eligible parcel (filter applied) AND every consolidation group.
Print line shows both counts."
```

---

## Phase C — Update Analyze

### Task 6: Include consolidation groups in `build_training_table`, drop their constituents from parcel rows

**Files:**
- Modify: `pipeline/analyze.py`
- Modify: `tests/test_pipeline_analyze.py`

Each consolidation group becomes one additional training row. Identifier `pin = f"group:{id}"` so the existing DataFrame schema (PK = pin) doesn't change. Label = 1 if any constituent PIN is in the positive_pins dict.

**Constituent drop (training only):** when a parcel is a constituent of a group that's in the training set, drop the parcel row from training. Each redevelopment event then contributes exactly once — through the group row, never through both the constituent and the group. **This is a TRAINING-ONLY change.** The parcels table is untouched; the constituent PINs still exist there and still get scored at score time via `score_parcels`. Multi-group constituents (a PIN that appears in multiple groups) are vanishingly rare per the user's call — the implementation just drops on first match.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_pipeline_analyze.py`:

```python
import json as _json


def test_build_training_table_includes_groups_and_drops_their_constituents(tmp_path):
    """A consolidation group becomes one training row. Its constituent PINs
    are DROPPED from the parcel-row training set so each redevelopment event
    contributes exactly once. Constituent PINs are NOT removed from the
    parcels table — this drop is training-only."""
    parcels = [
        _parcel_row("14210010010000", lot_size_sf=3000.0,
                    address="100 W FAKE ST", lat=41.93, lng=-87.65),
        _parcel_row("14210010020000", lot_size_sf=4000.0,
                    address="102 W FAKE ST", lat=41.93, lng=-87.65),
        # Unrelated parcel, kept as a regular parcel row
        _parcel_row("14210010030000", lot_size_sf=5000.0,
                    address="200 W OTHER ST", lat=41.94, lng=-87.66),
    ]
    db_path = _build_analyze_db(tmp_path, parcels)
    # Add a consolidation group whose constituents are PIN 1 and PIN 2
    from pipeline.db import get_connection
    conn = get_connection(db_path)
    try:
        conn.execute("""
            INSERT INTO consolidation_groups
              (group_id, pins, combined_lot_size_sf, combined_building_sf,
               owner_name, detected_date)
            VALUES (1, ?, 7000.0, NULL, 'TEST OWNER', '2026-04-28')
        """, (_json.dumps(["14210010010000", "14210010020000"]),))
        conn.commit()
    finally:
        conn.close()

    # PIN 1 had a qualifying permit; PIN 2 did not.
    positives = {"14210010010000": 2018}
    df = analyze.build_training_table(db_path, positives)

    # PIN_1 and PIN_2 are CONSTITUENTS of group 1 → dropped from parcel rows.
    # PIN_3 stays. Group 1 is added. Total = 1 parcel + 1 group = 2 rows.
    assert len(df) == 2
    pins = df["pin"].tolist()
    assert "14210010010000" not in pins   # constituent dropped
    assert "14210010020000" not in pins   # constituent dropped
    assert "14210010030000" in pins        # not in any group → kept
    assert "group:1" in pins

    by_pin = df.set_index("pin")
    # The group is positive because constituent PIN 1 is in positive_pins
    assert by_pin.loc["group:1", "label"] == 1
    # PIN 3 is negative (no permit, not in any group)
    assert by_pin.loc["14210010030000", "label"] == 0

    # Group's lot_size_sf is the COMBINED value (7000)
    assert by_pin.loc["group:1", "lot_size_sf"] == 7000.0

    # Important: the parcels table itself is unchanged — the constituents
    # still exist there with their original data. This is verified by
    # querying parcels directly:
    conn = get_connection(db_path)
    try:
        n_parcels = conn.execute("SELECT COUNT(*) FROM parcels").fetchone()[0]
    finally:
        conn.close()
    assert n_parcels == 3  # all three parcels still in the table


def test_build_training_table_funnel_records_constituent_drop(tmp_path):
    """The funnel exposes both the constituent-drop count and the
    consolidation-groups-added count so the report can show the math."""
    parcels = [
        _parcel_row("14210010010000"),  # constituent of group 1 → dropped
        _parcel_row("14210010020000"),  # constituent of group 1 → dropped
        _parcel_row("14210010030000"),  # standalone → kept
    ]
    db_path = _build_analyze_db(tmp_path, parcels)
    from pipeline.db import get_connection
    conn = get_connection(db_path)
    try:
        conn.execute("""
            INSERT INTO consolidation_groups
              (group_id, pins, combined_lot_size_sf, combined_building_sf,
               owner_name, detected_date)
            VALUES (1, ?, 7000.0, NULL, 'TEST OWNER', '2026-04-28')
        """, (_json.dumps(["14210010010000", "14210010020000"]),))
        conn.commit()
    finally:
        conn.close()

    df = analyze.build_training_table(db_path, positive_pins={})
    funnel = df.attrs["funnel"]
    assert funnel["after_condo_unit_drop"] == 3
    assert funnel["after_constituent_drop"] == 1  # 2 of 3 dropped (PIN_1, PIN_2)
    assert funnel["consolidation_groups_added"] == 1
    assert funnel["after_consolidation_group_add"] == 2  # 1 parcel + 1 group
```

- [ ] **Step 2: Run the failing test**

```bash
.venv/bin/pytest tests/test_pipeline_analyze.py::test_build_training_table_includes_consolidation_groups -v
```

Expected: FAIL — `len(df) == 3`, no `group:1` row.

- [ ] **Step 3: Update `build_training_table`**

In `pipeline/analyze.py`, add the import at the top:

```python
from pipeline.consolidation_features import derive_group_features
```

Modify `build_training_table` to append consolidation groups after the parcels eligibility filter. Insert this block right before the `df.attrs["funnel"] = funnel` line and right before `df.attrs["imputation_rates"] = imputation_rates` (which means before all the imputation work — we need to add groups before computing imputation medians, so groups participate in the median).

Replace the function body to add a group-collection step. The cleanest version:

```python
def build_training_table(
    db_path: Path,
    positive_pins: dict[str, int],
) -> pd.DataFrame:
    """Assemble one (features, label) row per eligible parcel and per
    consolidation group.

    Eligibility filter for parcels (in this order):
      1. has zone_class
      2. zone_class is not PD/PMD
      3. is_condo_unit = 0
      4. PIN not in raw_assessor_exempt
      5. PIN is NOT a constituent of any consolidation group in the
         training set (each redevelopment event contributes exactly once
         — through the group row, not also through the constituent row).
         This drop is training-only — the parcels table is untouched.

    Consolidation groups are added unconditionally — they have their own
    aggregated features regardless of the constituent parcels' filters.
    Identifier: pin = f"group:{id}" so the DataFrame schema is unchanged.
    Group label = 1 if any constituent PIN is in positive_pins, else 0.

    NULL handling after eligibility:
      - continuous: fill with training-set median (parcels + groups together)
      - binary: fill with 0
    """
    import json as _json
    columns = [s[0] for s in SIGNALS]
    select_cols = ["pin", "zone_class", "is_condo_unit"] + columns
    placeholders = ", ".join(select_cols)
    conn = get_connection(db_path)
    try:
        rows = [dict(r) for r in conn.execute(
            f"SELECT {placeholders} FROM parcels"
        ).fetchall()]
        exempt_pins = {
            r["pin"] for r in conn.execute(
                "SELECT pin FROM raw_assessor_exempt"
            ).fetchall()
        }
        group_records = [dict(r) for r in conn.execute(
            "SELECT group_id, pins FROM consolidation_groups"
        ).fetchall()]
    finally:
        conn.close()

    # Collect every PIN that's a constituent of a consolidation group.
    # These get dropped from the PARCEL training rows — they'll contribute
    # via their group row instead. (Multi-group constituents are vanishingly
    # rare — a flat set lookup is fine.)
    constituent_pins: set[str] = set()
    for gr in group_records:
        for pin in _json.loads(gr["pins"]):
            constituent_pins.add(pin)
    group_ids = [gr["group_id"] for gr in group_records]

    # Parcel eligibility funnel
    eligible_after_condo = []  # passes the original 4 filters
    for r in rows:
        if not r["zone_class"]:
            continue
        if _is_pd_zone(r["zone_class"]):
            continue
        if r["is_condo_unit"]:
            continue
        if r["pin"] in exempt_pins:
            continue
        eligible_after_condo.append(r)

    # Apply the constituent drop (training-only — DOES NOT touch the parcels table).
    eligible = [r for r in eligible_after_condo
                if r["pin"] not in constituent_pins]

    funnel = {
        "total_parcels": len(rows),
        "after_exempt_drop":   sum(1 for r in rows if r["pin"] not in exempt_pins),
        "after_no_zone_drop":  sum(1 for r in rows
                                   if r["pin"] not in exempt_pins and r["zone_class"]),
        "after_pd_drop":       sum(1 for r in rows
                                   if r["pin"] not in exempt_pins and r["zone_class"]
                                   and not _is_pd_zone(r["zone_class"])),
        "after_condo_unit_drop": len(eligible_after_condo),
        "after_constituent_drop": len(eligible),
        "consolidation_groups_added": len(group_ids),
        "after_consolidation_group_add": len(eligible) + len(group_ids),
    }

    # Build the DataFrame from parcels + groups.
    parcel_rows_for_df = [
        {**{c: r[c] for c in ["pin"] + columns}}
        for r in eligible
    ]

    group_rows_for_df = []
    for gid in group_ids:
        features = derive_group_features(gid, db_path)
        # Group label = 1 if any constituent had a qualifying permit
        constituent_pins_for_group = _group_constituent_pins(db_path, gid)
        label = int(any(p in positive_pins for p in constituent_pins_for_group))
        group_rows_for_df.append({
            "pin": f"group:{gid}",
            **features,
            "_group_label_override": label,
        })

    if not parcel_rows_for_df and not group_rows_for_df:
        df = pd.DataFrame(columns=["pin", "label"] + columns)
        df.attrs["funnel"] = funnel
        df.attrs["imputation_rates"] = {}
        return df

    df = pd.DataFrame(parcel_rows_for_df + group_rows_for_df)[
        ["pin"] + columns + (["_group_label_override"] if group_rows_for_df else [])
    ].copy()

    # Label: parcels via positive_pins membership; groups via override column
    if "_group_label_override" in df.columns:
        df["label"] = df["pin"].isin(positive_pins).astype(int)
        # For group rows, _group_label_override holds the precomputed label
        group_mask = df["pin"].str.startswith("group:")
        df.loc[group_mask, "label"] = df.loc[group_mask, "_group_label_override"].astype(int)
        df = df.drop(columns=["_group_label_override"])
    else:
        df["label"] = df["pin"].isin(positive_pins).astype(int)

    imputation_rates: dict[str, dict[str, float]] = {}
    n = len(df)
    for col, kind, _src in SIGNALS:
        nulls = df[col].isna().sum()
        pct = round(100.0 * nulls / n, 1) if n else 0.0
        imputation_rates[col] = {"n_imputed": int(nulls), "pct": pct}
        if kind == "continuous":
            median = df[col].median()
            df[col] = df[col].fillna(0 if pd.isna(median) else median)
        else:
            df[col] = df[col].fillna(0).astype(int)

    df.attrs["funnel"] = funnel
    df.attrs["imputation_rates"] = imputation_rates
    return df


def _group_constituent_pins(db_path: Path, group_id: int) -> list[str]:
    """Read a consolidation group's pins JSON array."""
    import json
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT pins FROM consolidation_groups WHERE group_id = ?",
            (group_id,),
        ).fetchone()
    finally:
        conn.close()
    return json.loads(row["pins"]) if row else []
```

- [ ] **Step 4: Run all analyze tests**

```bash
.venv/bin/pytest tests/test_pipeline_analyze.py -v
```

Expected: 21 passing (was 19, +2 new). The existing tests should still pass — they don't use consolidation_groups so the new code path is no-op for them.

- [ ] **Step 5: Full suite**

```bash
.venv/bin/pytest -q
```

Expected: **216 passing**.

- [ ] **Step 6: Commit**

```bash
git add pipeline/analyze.py tests/test_pipeline_analyze.py
git commit -m "feat(analyze): include consolidation groups in training, drop their constituents

Each consolidation group becomes one training row with derived features
from derive_group_features (per the locked aggregation rules). Identifier
'group:{id}' keeps the DataFrame schema unchanged. Group label=1 if any
constituent PIN had a qualifying permit.

Constituents of training groups are dropped from the parcel rows — each
redevelopment event contributes exactly once (via the group row, not also
via the constituent). This drop is TRAINING-ONLY: the parcels table is
untouched and constituent PINs still get scored at score time via
score_parcels.

Imputation medians now compute across both parcels AND groups, so the
lot_size normalization range captures the redevelopment-relevant scale."
```

---

### Task 7: Update the report writer to surface group counts

**Files:**
- Modify: `pipeline/analyze.py`
- Modify: `tests/test_pipeline_analyze.py`

Funnel section in the markdown report should show the new `consolidation_groups_added` and `after_consolidation_group_add` counts so the reader can see the training population includes both.

- [ ] **Step 1: Update the report writer test**

Modify `test_write_analysis_report_contains_required_sections` in `tests/test_pipeline_analyze.py`. Add to the `funnel` dict:

```python
    funnel = {"total_parcels": 67677, "after_exempt_drop": 67000,
              "after_no_zone_drop": 66800, "after_pd_drop": 64781,
              "after_condo_unit_drop": 17753,
              "after_constituent_drop": 14021,
              "consolidation_groups_added": 6864,
              "after_consolidation_group_add": 20885}
```

And add new assertions at the end:

```python
    # Funnel includes consolidation-group counts and constituent-drop step
    assert "After dropping constituents" in body
    assert "Consolidation groups added" in body
    assert "14,021" in body  # after constituent drop
    assert "6,864" in body
    assert "20,885" in body  # final training-set total
```

- [ ] **Step 2: Run the test (should still pass on the existing assertions, fail on the new ones)**

```bash
.venv/bin/pytest tests/test_pipeline_analyze.py::test_write_analysis_report_contains_required_sections -v
```

Expected: FAIL on the "Consolidation groups added" assertion.

- [ ] **Step 3: Update the report writer**

In `pipeline/analyze.py`, find the funnel rendering block in `write_analysis_report`. Replace it with:

```python
    a("## Eligibility funnel")
    a("")
    a("| Step | Parcels remaining |")
    a("|---|---|")
    a(f"| Total parcels in DB | {funnel['total_parcels']:,} |")
    a(f"| After dropping tax-exempt | {funnel['after_exempt_drop']:,} |")
    a(f"| After dropping no-zone-class | {funnel['after_no_zone_drop']:,} |")
    a(f"| After dropping PD-zoned | {funnel['after_pd_drop']:,} |")
    a(f"| After dropping condo units | {funnel['after_condo_unit_drop']:,} |")
    a(f"| After dropping constituents of training groups | {funnel.get('after_constituent_drop', funnel['after_condo_unit_drop']):,} |")
    a(f"| Consolidation groups added | +{funnel.get('consolidation_groups_added', 0):,} |")
    a(f"| Training set total | **{funnel.get('after_consolidation_group_add', funnel['after_condo_unit_drop']):,}** |")
    a("")
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest tests/test_pipeline_analyze.py -v
.venv/bin/pytest -q
```

Expected: all 21 analyze tests pass; **216 passing** total.

- [ ] **Step 5: Commit**

```bash
git add pipeline/analyze.py tests/test_pipeline_analyze.py
git commit -m "feat(analyze): report shows consolidation-group counts in funnel

Eligibility funnel now adds two rows: 'Consolidation groups added' (the +N
groups), and 'Training set total' (parcels + groups). Reader can see the
training population isn't parcels-only."
```

---

### Task 8: Bump scoring version prefix to 1.1.0

**Files:**
- Modify: `pipeline/analyze.py`

The methodology has changed materially (training set now includes groups; condo units excluded from scoring). Bump the version prefix so generated YAMLs are tagged accordingly.

- [ ] **Step 1: Update the constant**

In `pipeline/analyze.py`:

```python
SCORING_VERSION_PREFIX = "1.1.0"
```

(was `"1.0.0"`)

- [ ] **Step 2: Run the suite**

```bash
.venv/bin/pytest -q
```

Expected: 216 passing — no test asserts the exact prefix, so this is a free change.

- [ ] **Step 3: Commit**

```bash
git add pipeline/analyze.py
git commit -m "chore(analyze): bump SCORING_VERSION_PREFIX to 1.1.0

Methodology change: condo units excluded from scoring; consolidation groups
included in training. Generated YAMLs are tagged 1.1.0-YYYY-MM-DD so the
score_version column on parcels and consolidation_groups records which
methodology was used."
```

---

## Phase D — Re-run end-to-end on `data/full.db`

### Task 9: Re-run Analyze + Score against `data/full.db`

**Files:** None (reads + writes `data/full.db`, writes `config/scoring.yaml`, writes `docs/analysis/2026-04-28-initial-scoring-weights.md`).

- [ ] **Step 1: Confirm the schema migration applied**

```bash
cd /Users/hunterheyman/Claude/chicago-pipeline
.venv/bin/python -c "
import sqlite3
c = sqlite3.connect('data/full.db')
cols = [r[1] for r in c.execute('PRAGMA table_info(consolidation_groups)').fetchall()]
print('consolidation_groups columns:', cols)
"
```

If `score` and `score_version` are NOT listed, run `init_db` once to apply the migration:

```bash
.venv/bin/python -c "
from pathlib import Path
from pipeline.db import init_db
init_db(Path('data/full.db'))
print('migration applied')
"
```

Re-confirm columns are present.

- [ ] **Step 2: Re-run Analyze**

```bash
.venv/bin/python -m pipeline.analyze \
    --db data/full.db \
    --config-dir config \
    --scoring-yaml config/scoring.yaml \
    --report-md docs/analysis/2026-04-28-initial-scoring-weights.md
```

Expected: exits 0, prints `Wrote config/scoring.yaml` etc. Wall time ~2-3 minutes (slightly longer than the parcels-only run because of group feature derivation across ~6,800 groups). Version stamp on the YAML should be `1.1.0-2026-04-28`.

If it errors on `derive_group_features` for a malformed group: investigate, fix in the relevant Phase A task, do NOT silently skip groups.

- [ ] **Step 3: Spot-check the new YAML**

```bash
head -50 config/scoring.yaml
```

Check:
- `version: "1.1.0-2026-04-28"`
- `top_n: 20`
- All 17 signals present.
- `lot_size_sf` normalization range — should now be MUCH wider than the prior run's `[3180, 8369]`. Expect something like `[2500, 50000+]` because consolidation groups have much bigger lots. If `normalization_max` is still under 12000, something is wrong with how groups are flowing through.

- [ ] **Step 4: Read the new report**

Open `docs/analysis/2026-04-28-initial-scoring-weights.md`. Verify:
- Funnel shows `Consolidation groups added: +6,864` (or similar, depending on the live count).
- `Training set total` is parcels + groups (~24,000+).
- Per-signal distribution table — `lot_size_sf` rows show much higher means/medians than before.
- Top-5 by weight — may or may not include `lot_size_sf` as significant now (it might be, with the wider distribution).
- Caveats section is still intact.

- [ ] **Step 5: Run Score against `data/full.db`**

```bash
.venv/bin/python -m pipeline.score --db data/full.db --scoring-yaml config/scoring.yaml
```

Expected: prints `Scored N parcels and M consolidation groups with version 1.1.0-2026-04-28`. N should be 67,677 minus condo units (~67677 - 44547 = ~23,130). M should be ~6,864.

- [ ] **Step 6: Sanity-check the new scores**

```bash
.venv/bin/python -c "
import sqlite3
c = sqlite3.connect('data/full.db')
c.row_factory = sqlite3.Row

# Parcels
n_total = c.execute('SELECT COUNT(*) FROM parcels').fetchone()[0]
n_scored = c.execute('SELECT COUNT(*) FROM parcels WHERE score IS NOT NULL').fetchone()[0]
n_condo = c.execute('SELECT COUNT(*) FROM parcels WHERE is_condo_unit = 1').fetchone()[0]
print(f'parcels: total={n_total:,}  scored={n_scored:,}  condo_units (should be NULL)={n_condo:,}')

stats = c.execute('SELECT MIN(score), MAX(score), AVG(score) FROM parcels WHERE score IS NOT NULL').fetchone()
print(f'parcel score min={stats[0]:.2f} max={stats[1]:.2f} mean={stats[2]:.2f}')

# Groups
g_total = c.execute('SELECT COUNT(*) FROM consolidation_groups').fetchone()[0]
g_scored = c.execute('SELECT COUNT(*) FROM consolidation_groups WHERE score IS NOT NULL').fetchone()[0]
print(f'consolidation_groups: total={g_total:,}  scored={g_scored:,}')

g_stats = c.execute('SELECT MIN(score), MAX(score), AVG(score) FROM consolidation_groups WHERE score IS NOT NULL').fetchone()
print(f'group score min={g_stats[0]:.2f} max={g_stats[1]:.2f} mean={g_stats[2]:.2f}')

# Top 5 across BOTH parcels and groups (combined ranking)
print('\nTop 10 across parcels + groups:')
combined = []
for r in c.execute('SELECT pin as id, address, score, \"parcel\" as kind FROM parcels WHERE score IS NOT NULL ORDER BY score DESC LIMIT 30').fetchall():
    combined.append((r['id'], r['address'], r['score'], r['kind']))
for r in c.execute('SELECT group_id as id, owner_name, score, \"group\" as kind FROM consolidation_groups WHERE score IS NOT NULL ORDER BY score DESC LIMIT 30').fetchall():
    combined.append((str(r['id']), r['owner_name'] or 'NULL', r['score'], r['kind']))
combined.sort(key=lambda x: -x[2])
for id_, name, sc, kind in combined[:10]:
    print(f'  {sc:.2f}  {kind:>6}  {id_}  {name[:60] if name else \"\"}')
"
```

Expected:
- `n_scored == n_total - n_condo` — only non-condo-unit parcels got scores.
- `condo_units (should be NULL) == 44547` (or similar) — confirms the filter applied.
- `g_scored == g_total` — every group scored.
- Top 10 list — should now include actual consolidation groups owned by real developers (DePaul, Strategic Belmont & St-Louis, Hoover Partners, etc.) rather than just unit-PINs at 525 W Hawthorne.

- [ ] **Step 7: Commit deliverables**

```bash
git add config/scoring.yaml docs/analysis/2026-04-28-initial-scoring-weights.md
git commit -m "feat(analyze): land 1.1.0 weights — condo units excluded, groups included

Re-run output of pipeline.analyze on data/full.db with the new training
population: condo units excluded, consolidation groups included as their
own training rows. Methodology version 1.1.0. The lot_size normalization
range widens substantially because consolidation groups (median 37,564 sf)
are now in the training distribution alongside standard residential lots."
```

- [ ] **Step 8: Final summary report**

Provide:
1. Number of parcels scored vs. before — should drop from 67,677 to ~23,130.
2. Number of consolidation groups scored — first time, ~6,864.
3. New top 5 across parcels + groups combined.
4. The new lot_size_sf normalization range vs. the old `[3180, 8369]`.
5. Top 5 signals by weight magnitude in the new YAML — likely different from the old run.
6. List of insignificant signals in the new YAML.

---

## Phase E — Documentation

### Task 10: Document the substitute-YAML workflow

**Files:**
- Modify: `chicago-pipeline/README.md` (or create if it doesn't exist with this content)

The CLI already supports `--scoring-yaml /any/path.yaml`. Document the workflow so it's discoverable.

- [ ] **Step 1: Check the current README**

```bash
ls chicago-pipeline/README.md && head -20 chicago-pipeline/README.md
```

If a README exists, append a new section. If not, create one. Either way, add this section:

```markdown
## Working with alternative scoring weights

The standard pipeline produces `config/scoring.yaml` from the historical-analysis
script. You can also create alternative YAMLs to experiment with different
weights, score sub-populations differently, or compare hypothetical weight sets
against the analysis-derived ones.

### Creating an alternative YAML

The scoring YAML format:

```yaml
version: "experimental-2026-04-28-no-tax"
generated_at: "2026-04-28T12:00:00+00:00"
top_n: 20
signals:
  lot_size_sf:
    kind: continuous
    weight: 0.5
    direction: positive
    normalization: { min: 1500.0, max: 50000.0 }
    insignificant: false
  is_llc:
    kind: binary
    weight: 0.5
    direction: positive
    normalization: { min: 0.0, max: 1.0 }
    insignificant: false
```

**Required per signal:** `kind` (`continuous`|`binary`), `weight` (float, sums to 1.0
across non-insignificant signals), `direction` (`positive`|`negative`),
`normalization.min` and `.max`, `insignificant` (boolean).

**Required top-level:** `version` (string — used as `score_version` on every
scored row), `top_n` (int), `signals` (mapping).

### Running with an alternative YAML

```bash
.venv/bin/python -m pipeline.score \
    --db data/full.db \
    --scoring-yaml path/to/alternative.yaml
```

This overwrites the `score` and `score_version` columns on every eligible
parcel and on every consolidation group. The score_version reflects the YAML
you used, so you can tell which weights produced any given score.

### Convention

Alternative YAMLs go in `config/scoring_alternatives/<name>.yaml` so they're
visible alongside the canonical one but don't get confused with it. Example
names: `no_tax_signals.yaml`, `motivation_only.yaml`, `consolidation_focus.yaml`.

### Comparing two scored DBs

To compare two weight sets without losing the previous run's scores, copy the
DB first:

```bash
cp data/full.db data/full.alt.db
.venv/bin/python -m pipeline.score --db data/full.alt.db --scoring-yaml config/scoring_alternatives/foo.yaml
.venv/bin/python -c "
import sqlite3
a = sqlite3.connect('data/full.db')
b = sqlite3.connect('data/full.alt.db')
# top 20 from canonical
canonical = {r[0]: r[1] for r in a.execute('SELECT pin, score FROM parcels ORDER BY score DESC LIMIT 20').fetchall()}
alternative = {r[0]: r[1] for r in b.execute('SELECT pin, score FROM parcels ORDER BY score DESC LIMIT 20').fetchall()}
print('In canonical top-20 but not alternative:', set(canonical) - set(alternative))
print('In alternative top-20 but not canonical:', set(alternative) - set(canonical))
"
```
```

- [ ] **Step 2: Verify the README renders correctly**

```bash
head -100 chicago-pipeline/README.md
```

Confirm the new section is intact and the markdown formatting is clean.

- [ ] **Step 3: Commit**

```bash
git add chicago-pipeline/README.md
git commit -m "docs: substitute-YAML workflow for scoring

The --scoring-yaml flag accepts arbitrary paths. Documents the YAML format,
the recommended config/scoring_alternatives/ convention, and how to compare
two weight sets via separate DB copies."
```

---

## Self-review checklist

After all tasks land, walk back through the spec:

- [x] **Skip individual condo units at score time:** Task 3 (`COALESCE(is_condo_unit, 0) = 0` filter).
- [x] **Score condo building reps:** Task 3 (the filter keeps `is_condo_building=1` rows because they have `is_condo_unit=0`).
- [x] **Score consolidation groups:** Tasks 1, 2, 4, 5.
- [x] **Per-signal aggregation rules:** Task 1 implements all 18 rules from the locked spec.
- [x] **Re-train Analyze with consolidation groups in training:** Task 6.
- [x] **Lot-size normalization range broadens:** Task 9 verifies — should expand from `[3180, 8369]` to something far wider.
- [x] **Substitute YAML documentation:** Task 10.
- [x] **Tests stay green:** every task ends with `.venv/bin/pytest -q`. Test count grows from 205 → ~218.
- [x] **TDD throughout:** every code-bearing task is test-first.
- [x] **Fixture-driven tests:** Tasks 1-8 use synthetic DBs via the existing `_build_*_db` helpers; only Task 9 touches `data/full.db`.
- [x] **Methodology version bump:** Task 8.
- [x] **Schema migration is non-destructive:** Task 2 uses `_LATER_COLUMNS` mechanism; existing data/full.db gains the columns on next `init_db()` call.

No placeholders, every code block self-contained.

---

## Out of scope (explicitly)

These are NOT in this plan; flag them as separate follow-ups if needed:

- **Surfacing consolidation_groups.score in the Review UI.** The UI currently sorts parcels by score; making it sort BOTH parcels and groups in a unified ranked list is a separate UI plan.
- **Fixing the RS-1 zoning anomaly at 525 W Hawthorne.** The user confirmed it's legal non-conforming; no action needed.
- **Adjusting the per-signal aggregation rules after seeing the live results.** The rules are locked for v1; if the rerun produces results that don't match intuition, that's a follow-up.
- **Deduplicating top-N to building footprint.** A condo high-rise's individual unit-PINs (now skipped) plus the building rep (now scored) plus the building's consolidation group (if any) could all appear in different forms — the UI's job is to present them sensibly, not the scoring's.
- **Writing `score` and `score_version` to the consolidation_groups schema in `SCHEMA_SQL` (Task 2 already does this) AND clearing them on re-consolidation.** If `pipeline/consolidate.py` ever DELETEs and recreates groups, the new groups won't have scores until `score()` is re-run. This is fine — the user can re-run on demand.
