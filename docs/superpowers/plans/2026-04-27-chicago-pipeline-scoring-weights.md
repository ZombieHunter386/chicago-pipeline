# Chicago Pipeline — Analyze (Initial Scoring Weights) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Analyze step of the Chicago pipeline — a historical-analysis script that derives initial weights for `config/scoring.yaml` by training a logistic-regression classifier on parcels that *did* get NEW CONSTRUCTION or WRECKING/DEMOLITION permits (positives) vs. parcels that didn't (negatives) inside the target geography. Run it once against `data/full.db` and emit two artifacts: `config/scoring.yaml` (weights, normalization ranges, top_n) and `docs/analysis/2026-04-27-initial-scoring-weights.md` (sample sizes, distribution comparisons, coefficients with bootstrap CIs, rationale).

**Architecture:**
- One new module `pipeline/analyze.py` with pure-function helpers and one orchestrator `analyze(db_path, geo, scoring_yaml_path, report_md_path)`.
- Positives identified by re-matching `raw_cdp_permits` rows (filtered to NEW CONSTRUCTION + WRECKING/DEMOLITION) to PINs via the existing `match_records_to_parcels_with_address` helper.
- Negatives are every other parcel in the target geography after dropping tax-exempt, PD-zoned, condo-unit (not building-rep), and parcels with no `zone_class`.
- Features come from the *current* `parcels` table (v1 simplification — see §"Snapshot fidelity caveat" in the report). One row per PIN. NULLs are median-imputed for continuous features and `0`-imputed for binary features.
- Logistic regression via `sklearn.linear_model.LogisticRegression(class_weight='balanced')` on z-scored continuous features (so coefficients are comparable). Confidence intervals via 200-iteration bootstrap on the training set (no `statsmodels` dependency).
- Weights = `|standardized coefficient| / sum(|coefs|)`, with a `direction` field (`positive`/`negative`) carrying the sign. Signals whose 95% bootstrap CI crosses 0 are flagged `insignificant: true` and written with `weight: 0`.
- Normalization ranges = 5th–95th percentile of each continuous signal across the training set (clipped on read in the Score step that lands next milestone).

**Tech Stack:** Python 3.12+, SQLite, pandas, numpy, scikit-learn, pyyaml. Working dir: `/Users/hunterheyman/Claude/chicago-pipeline`. Run pytest as `.venv/bin/pytest`.

**Verification baseline:** before starting, run `.venv/bin/pytest -q` from `chicago-pipeline/` — must show **163 passing**. After every task, all tests must still pass. The final task runs the full module against `data/full.db` and captures the deliverables.

**Out of scope** (will be its own next plan, see §"Score Plan Scope" at the bottom):
- Implementing the Score step that consumes `config/scoring.yaml`.
- Reconstructing per-PIN pre-development snapshots (v1 uses current state — documented as a limitation).
- Fetching new data (no API calls; we read `data/full.db` only).
- Tuning the weights from outreach feedback (that's the Feedback Loop, much later).

---

## Phase 0 — Scaffolding

### Task 1: Add scikit-learn to requirements.txt

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Append the dependency**

Edit `requirements.txt` to add the pinned line at the bottom (matching the existing pin style):

```
scikit-learn==1.5.2
```

- [ ] **Step 2: Install into the existing venv**

Run: `.venv/bin/pip install -r requirements.txt`

Expected: `scikit-learn` installed without conflicts. Numpy and scipy come along as transitive deps.

- [ ] **Step 3: Sanity-check the import**

Run: `.venv/bin/python -c "from sklearn.linear_model import LogisticRegression; print(LogisticRegression().get_params()['class_weight'])"`

Expected output: `None` (the default — confirms the import works).

- [ ] **Step 4: Commit**

```bash
git add requirements.txt
git commit -m "deps: add scikit-learn for the historical-analysis script"
```

---

### Task 2: Create `pipeline/analyze.py` with the public API stub and signal spec

**Files:**
- Create: `pipeline/analyze.py`
- Test: `tests/test_pipeline_analyze.py`

This task lays down the module skeleton: the `SIGNALS` registry that drives every downstream task, the `analyze()` entry-point signature, and a single trivial test. No real logic yet — that comes in Tasks 3-9. The point is to lock in the public surface so subsequent tasks can import the names without churn.

- [ ] **Step 1: Write the failing test**

Create `tests/test_pipeline_analyze.py`:

```python
"""Tests for pipeline/analyze.py — the historical-analysis script that derives
initial scoring weights from permit history."""
from pipeline import analyze


def test_signals_registry_shape():
    """SIGNALS is the single source of truth for what features the model sees.
    Every entry must be a 3-tuple of (column_name, kind, source_table) where
    kind is 'continuous' or 'binary'."""
    assert len(analyze.SIGNALS) > 0
    for entry in analyze.SIGNALS:
        assert len(entry) == 3
        col, kind, source = entry
        assert isinstance(col, str) and col
        assert kind in ("continuous", "binary")
        assert source == "parcels", \
            f"{col}: only the parcels table is supported in v1"


def test_signals_excludes_known_bad_columns():
    """tax_delinquent has 0% population on data/full.db (CSV is a stub).
    has_vacancy_report uses a defunct legacy dataset. Both must NOT be in
    the v1 feature set."""
    cols = [s[0] for s in analyze.SIGNALS]
    assert "tax_delinquent" not in cols
    assert "has_vacancy_report" not in cols


def test_analyze_entry_point_exists():
    """The orchestrator must accept (db_path, geo, scoring_yaml_path, report_md_path)."""
    import inspect
    sig = inspect.signature(analyze.analyze)
    assert list(sig.parameters) == ["db_path", "geo", "scoring_yaml_path", "report_md_path"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/pytest tests/test_pipeline_analyze.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'pipeline.analyze'`.

- [ ] **Step 3: Create the stub module**

Create `pipeline/analyze.py`:

```python
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
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/pytest tests/test_pipeline_analyze.py -v`

Expected: 3 tests PASS.

- [ ] **Step 5: Confirm full suite still passes**

Run: `.venv/bin/pytest -q`

Expected: **166 passing** (was 163, +3 new).

- [ ] **Step 6: Commit**

```bash
git add pipeline/analyze.py tests/test_pipeline_analyze.py
git commit -m "feat(analyze): scaffold pipeline/analyze.py + SIGNALS registry

Public surface only — entry-point raises NotImplementedError. Subsequent tasks
fill in positive identification, training-set construction, logistic regression,
weight derivation, and output writers."
```

---

## Phase 1 — Identify positive examples

### Task 3: `identify_positive_examples(db_path)` — match qualifying permits to PINs

**Files:**
- Modify: `pipeline/analyze.py`
- Modify: `tests/test_pipeline_analyze.py`
- Helper: a per-test DB builder we'll reuse across Tasks 3-9. Add it to the test file rather than `conftest.py` to keep it scoped.

The cdp_permits source already populates `parcels.years_since_last_permit` from *any* permit type — that's not what we need. Here we re-match raw permit rows (filtered to NEW CONSTRUCTION / WRECKING-DEMOLITION) and return `{pin: earliest_qualifying_permit_year}`. "Earliest" matters because that's the *event year* — if the same PIN has both a 2009 demo and a 2011 new-build, the snapshot should be from before the demo.

- [ ] **Step 1: Write the failing test (and the shared DB builder)**

Append to `tests/test_pipeline_analyze.py`:

```python
import sqlite3
from pathlib import Path
from datetime import datetime, UTC

from pipeline.db import init_db, upsert_rows


def _build_analyze_db(tmp_path: Path, parcels: list[dict], permits: list[dict] | None = None,
                      values: list[dict] | None = None,
                      exempt: list[dict] | None = None) -> Path:
    """Create a fresh SQLite DB with init_db() schema and insert the given rows
    directly into raw_/parcels tables. No fetch flow — these tests don't care
    about Socrata wiring; they care about the analyze logic."""
    db_path = tmp_path / "analyze.db"
    init_db(db_path)
    now = datetime.now(UTC).isoformat(timespec="seconds")
    if parcels:
        upsert_rows(db_path, "parcels",
                    [{**p, "last_fetched_date": now} for p in parcels],
                    key_columns=["pin"])
    if permits:
        upsert_rows(db_path, "raw_cdp_permits",
                    [{**p, "fetched_at": now} for p in permits],
                    key_columns=["permit_number"])
    if values:
        upsert_rows(db_path, "raw_assessor_values",
                    [{**v, "fetched_at": now} for v in values],
                    key_columns=["pin", "year"])
    if exempt:
        upsert_rows(db_path, "raw_assessor_exempt",
                    [{**e, "fetched_at": now} for e in exempt],
                    key_columns=["pin"])
    return db_path


def test_identify_positive_examples_filters_permit_types(tmp_path):
    """Only NEW CONSTRUCTION and WRECKING/DEMOLITION qualify. Reroofs,
    renovations, and electrical permits don't move parcels into the positive
    set — those happen routinely on long-held property without redevelopment."""
    parcels = [
        {"pin": "14210010010000", "address": "100 W DIVERSEY PKWY",
         "lat": 41.94001, "lng": -87.65001},
        {"pin": "14210010020000", "address": "200 N HALSTED",
         "lat": 41.93001, "lng": -87.66001},
        {"pin": "14210010030000", "address": "300 W FULLERTON AVE",
         "lat": 41.92501, "lng": -87.65501},
    ]
    permits = [
        # PIN 1 — NEW CONSTRUCTION 2018, qualifies
        {"permit_number": "p1", "permit_type": "PERMIT - NEW CONSTRUCTION",
         "issue_date": "2018-05-12",
         "street_number": "100", "street_direction": "W", "street_name": "DIVERSEY PKWY",
         "latitude": 41.94001, "longitude": -87.65001},
        # PIN 1 — earlier demo 2014, qualifies and supersedes the 2018 one
        # (we want the EVENT year — earliest qualifying permit)
        {"permit_number": "p2", "permit_type": "PERMIT - WRECKING/DEMOLITION",
         "issue_date": "2014-08-22",
         "street_number": "100", "street_direction": "W", "street_name": "DIVERSEY PKWY",
         "latitude": 41.94001, "longitude": -87.65001},
        # PIN 2 — RENOVATION ONLY, does NOT qualify
        {"permit_number": "p3", "permit_type": "PERMIT - RENOVATION/ALTERATION",
         "issue_date": "2020-01-15",
         "street_number": "200", "street_direction": "N", "street_name": "HALSTED",
         "latitude": 41.93001, "longitude": -87.66001},
        # PIN 3 — ELECTRIC WIRING, does NOT qualify
        {"permit_number": "p4", "permit_type": "PERMIT - ELECTRIC WIRING",
         "issue_date": "2021-06-01",
         "street_number": "300", "street_direction": "W", "street_name": "FULLERTON AVE",
         "latitude": 41.92501, "longitude": -87.65501},
    ]
    db_path = _build_analyze_db(tmp_path, parcels, permits)

    result = analyze.identify_positive_examples(db_path)

    # Only PIN 1 qualifies, and the year is the earlier 2014 demo, not the 2018 build.
    assert result == {"14210010010000": 2014}


def test_identify_positive_examples_handles_address_match(tmp_path):
    """Permits with no lat/lng but matching street address should still match
    via the address-first matcher (Tier 1)."""
    parcels = [
        {"pin": "14210010010000", "address": "100 W DIVERSEY PKWY",
         "lat": 41.94001, "lng": -87.65001},
    ]
    permits = [
        # No lat/lng, but the street_number/direction/name reconstruct to the parcel address.
        {"permit_number": "p1", "permit_type": "PERMIT - NEW CONSTRUCTION",
         "issue_date": "2019-07-04",
         "street_number": "100", "street_direction": "W", "street_name": "DIVERSEY PKWY",
         "latitude": None, "longitude": None},
    ]
    db_path = _build_analyze_db(tmp_path, parcels, permits)
    result = analyze.identify_positive_examples(db_path)
    assert result == {"14210010010000": 2019}


def test_identify_positive_examples_returns_empty_when_no_qualifying_permits(tmp_path):
    parcels = [{"pin": "14210010010000", "address": "100 W DIVERSEY PKWY",
                "lat": 41.94001, "lng": -87.65001}]
    permits = [
        {"permit_number": "p1", "permit_type": "PERMIT - RENOVATION/ALTERATION",
         "issue_date": "2020-01-01",
         "street_number": "100", "street_direction": "W", "street_name": "DIVERSEY PKWY",
         "latitude": 41.94001, "longitude": -87.65001},
    ]
    db_path = _build_analyze_db(tmp_path, parcels, permits)
    assert analyze.identify_positive_examples(db_path) == {}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/pytest tests/test_pipeline_analyze.py::test_identify_positive_examples_filters_permit_types -v`

Expected: FAIL with `AttributeError: module 'pipeline.analyze' has no attribute 'identify_positive_examples'`.

- [ ] **Step 3: Implement `identify_positive_examples`**

Add to `pipeline/analyze.py` (above the `analyze()` entry-point):

```python
import sqlite3

from pipeline.db import get_connection
from pipeline.spatial import (
    DEFAULT_GEO_RADIUS_FT,
    match_records_to_parcels_with_address,
)


# Permit types that count as a development event. Match by prefix because the
# raw permit_type strings vary slightly ("PERMIT - NEW CONSTRUCTION", sometimes
# trailing whitespace or sub-type qualifiers).
QUALIFYING_PERMIT_PREFIXES = (
    "PERMIT - NEW CONSTRUCTION",
    "PERMIT - WRECKING/DEMOLITION",
)


def _is_qualifying_permit(permit_type: str | None) -> bool:
    if not permit_type:
        return False
    pt = permit_type.strip().upper()
    return any(pt.startswith(p) for p in QUALIFYING_PERMIT_PREFIXES)


def _permit_record_address(r: dict) -> str | None:
    """Same address builder used by sources/cdp_permits.py — kept duplicated
    here to keep analyze decoupled from the fetch source modules."""
    parts = [
        (r.get("street_number") or "").strip(),
        (r.get("street_direction") or "").strip(),
        (r.get("street_name") or "").strip(),
    ]
    parts = [p for p in parts if p]
    return " ".join(parts) if parts else None


def identify_positive_examples(db_path: Path) -> dict[str, int]:
    """Find PINs with at least one NEW CONSTRUCTION or WRECKING/DEMOLITION
    permit in raw_cdp_permits. Returns {pin: earliest_qualifying_year}.

    "Earliest" because the *event year* is the redevelopment trigger; if a
    PIN had both a demo and a follow-up new-build, the demo's year is the
    boundary the pre-development snapshot should sit before.
    """
    conn = get_connection(db_path)
    try:
        permit_rows = [dict(r) for r in conn.execute(
            "SELECT permit_number, permit_type, issue_date, "
            "       street_number, street_direction, street_name, latitude, longitude "
            "FROM raw_cdp_permits"
        ).fetchall()]
        parcels = [dict(r) for r in conn.execute(
            "SELECT pin, address, lat, lng FROM parcels"
        ).fetchall()]
    finally:
        conn.close()

    qualifying = [r for r in permit_rows if _is_qualifying_permit(r["permit_type"])]
    if not qualifying or not parcels:
        return {}

    matches, _fuzzy = match_records_to_parcels_with_address(
        qualifying, parcels,
        get_record_address=_permit_record_address,
        geo_radius_ft=DEFAULT_GEO_RADIUS_FT,
    )

    earliest: dict[str, int] = {}
    for idx, (pin, _method) in matches.items():
        date_str = qualifying[idx]["issue_date"]
        if not date_str:
            continue
        year = int(date_str[:4])
        if pin not in earliest or year < earliest[pin]:
            earliest[pin] = year
    return earliest
```

- [ ] **Step 4: Run the new tests**

Run: `.venv/bin/pytest tests/test_pipeline_analyze.py -v`

Expected: 6 tests PASS (3 from Task 2, 3 new).

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/pytest -q`

Expected: **169 passing**.

- [ ] **Step 6: Commit**

```bash
git add pipeline/analyze.py tests/test_pipeline_analyze.py
git commit -m "feat(analyze): identify positive examples from qualifying permits

NEW CONSTRUCTION and WRECKING/DEMOLITION are the redevelopment events. We
re-match the raw_cdp_permits rows (not just the derived years_since_last_permit
column) because we need the event year per PIN, and we want only the
qualifying permit types — not any permit. Reuses the address-first matcher
shipped in the audit branch."
```

---

## Phase 2 — Build the training set

### Task 4: `build_training_table(db_path, positive_pins)` — assemble (features, label) frame

**Files:**
- Modify: `pipeline/analyze.py`
- Modify: `tests/test_pipeline_analyze.py`

The training table is one row per parcel with all `SIGNALS` columns + a binary `label` column. We drop:
- Tax-exempt PINs (`raw_assessor_exempt`) — they aren't acquisition targets and skew the negatives.
- Condo *units* (`is_condo_unit = 1`) — too noisy and the assessor characteristics dataset doesn't cover them; building reps stay.
- Parcels with no `zone_class` (POS / mistyped strings).
- PD-zoned parcels (`zone_class LIKE 'PD%'`) — `max_far` is NULL for them and the features `max_far` / `far_gap_delta` / `allows_multifamily_by_right` would all be missing in correlated ways. Dropping is cleaner than imputing.

After dropping, NULLs in continuous features are median-imputed using the **training set median** (not the global parcels median — the training set is what we model on); NULLs in binary features become `0`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_pipeline_analyze.py`:

```python
import pandas as pd


def _parcel_row(pin, **overrides):
    """Helper: minimum-viable parcels row with sensible defaults so individual
    tests only spell out the fields they care about."""
    base = {
        "pin": pin, "address": f"{pin[-4:]} W FAKE ST", "lat": 41.93, "lng": -87.65,
        "lot_size_sf": 5000.0, "hold_duration_years": 10.0,
        "max_far": 2.0, "far_gap_delta": 0.5, "land_building_ratio": 0.4,
        "estimated_annual_tax": 12000.0, "tax_increase_pct_5yr": 15.0,
        "cta_distance_ft": 2000.0, "appeal_count": 1, "open_violations_count": 0,
        "years_since_last_permit": 5.0, "vacant_violations_count": 0,
        "scofflaw_appearances_count": 0,
        "is_absentee": 0, "is_llc": 0, "allows_multifamily_by_right": 1,
        "is_scofflaw": 0, "is_condo_unit": 0, "zone_class": "RM-5",
    }
    base.update(overrides)
    return base


def test_build_training_table_basic_shape(tmp_path):
    parcels = [_parcel_row("14210010010000"), _parcel_row("14210010020000")]
    db_path = _build_analyze_db(tmp_path, parcels)
    positives = {"14210010010000": 2018}
    df = analyze.build_training_table(db_path, positives)
    assert len(df) == 2
    assert "label" in df.columns
    # Order isn't guaranteed; check by PIN.
    by_pin = df.set_index("pin")
    assert by_pin.loc["14210010010000", "label"] == 1
    assert by_pin.loc["14210010020000", "label"] == 0
    # Every signal column must be present.
    for col, _kind, _src in analyze.SIGNALS:
        assert col in df.columns


def test_build_training_table_drops_tax_exempt(tmp_path):
    parcels = [_parcel_row("14210010010000"), _parcel_row("14210010020000")]
    exempt = [{"pin": "14210010020000", "exemption_type": "Religious"}]
    db_path = _build_analyze_db(tmp_path, parcels, exempt=exempt)
    df = analyze.build_training_table(db_path, positive_pins={})
    assert df["pin"].tolist() == ["14210010010000"]


def test_build_training_table_drops_pd_zoned(tmp_path):
    parcels = [
        _parcel_row("14210010010000", zone_class="RM-5"),
        _parcel_row("14210010020000", zone_class="PD 555", max_far=None,
                    allows_multifamily_by_right=None, far_gap_delta=None),
    ]
    db_path = _build_analyze_db(tmp_path, parcels)
    df = analyze.build_training_table(db_path, positive_pins={})
    assert df["pin"].tolist() == ["14210010010000"]


def test_build_training_table_drops_condo_units_keeps_building_reps(tmp_path):
    parcels = [
        _parcel_row("14210010010000", is_condo_unit=0),
        _parcel_row("14210010020000", is_condo_unit=1),
    ]
    db_path = _build_analyze_db(tmp_path, parcels)
    df = analyze.build_training_table(db_path, positive_pins={})
    assert df["pin"].tolist() == ["14210010010000"]


def test_build_training_table_imputes_nulls(tmp_path):
    """Continuous NULLs become the training-set median; binary NULLs become 0.
    Imputed cells are tracked so the report can disclose the imputation rate."""
    parcels = [
        _parcel_row("14210010010000", lot_size_sf=4000.0, is_absentee=1),
        _parcel_row("14210010020000", lot_size_sf=8000.0, is_absentee=None),
        _parcel_row("14210010030000", lot_size_sf=None,    is_absentee=None),
    ]
    db_path = _build_analyze_db(tmp_path, parcels)
    df = analyze.build_training_table(db_path, positive_pins={})
    # Median of (4000, 8000) = 6000
    by_pin = df.set_index("pin")
    assert by_pin.loc["14210010030000", "lot_size_sf"] == 6000.0
    # Binary NULLs go to 0
    assert by_pin.loc["14210010020000", "is_absentee"] == 0
    assert by_pin.loc["14210010030000", "is_absentee"] == 0
    # Imputation rate exposed via attrs (consumed by the report writer)
    rates = df.attrs["imputation_rates"]
    assert rates["lot_size_sf"]["pct"] == round(100 / 3, 1)  # 1 of 3 imputed
    assert rates["is_absentee"]["pct"] == round(200 / 3, 1)  # 2 of 3 imputed
```

- [ ] **Step 2: Run the failing test**

Run: `.venv/bin/pytest tests/test_pipeline_analyze.py::test_build_training_table_basic_shape -v`

Expected: FAIL with `AttributeError: module 'pipeline.analyze' has no attribute 'build_training_table'`.

- [ ] **Step 3: Implement `build_training_table`**

Add to `pipeline/analyze.py`:

```python
import pandas as pd


def _is_pd_zone(zone_class: str | None) -> bool:
    if not zone_class:
        return False
    return zone_class.strip().upper().startswith("PD")


def build_training_table(
    db_path: Path,
    positive_pins: dict[str, int],
) -> pd.DataFrame:
    """Assemble one (features, label) row per eligible parcel.

    Eligibility filter (in this order):
      1. has zone_class
      2. zone_class is not PD/PMD (no max_far ordinance available)
      3. is_condo_unit = 0  (units excluded; building reps kept)
      4. PIN not in raw_assessor_exempt

    NULL handling after eligibility:
      - continuous: fill with training-set median
      - binary: fill with 0

    Imputation rates are attached to df.attrs['imputation_rates'] for the
    report writer.
    """
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
    finally:
        conn.close()

    eligible = []
    for r in rows:
        if not r["zone_class"]:
            continue
        if _is_pd_zone(r["zone_class"]):
            continue
        if r["is_condo_unit"]:
            continue
        if r["pin"] in exempt_pins:
            continue
        eligible.append(r)

    if not eligible:
        df = pd.DataFrame(columns=["pin", "label"] + columns)
        df.attrs["imputation_rates"] = {}
        return df

    df = pd.DataFrame(eligible)[["pin"] + columns].copy()
    df["label"] = df["pin"].isin(positive_pins).astype(int)

    imputation_rates: dict[str, dict[str, float]] = {}
    n = len(df)
    for col, kind, _src in SIGNALS:
        nulls = df[col].isna().sum()
        pct = round(100.0 * nulls / n, 1) if n else 0.0
        imputation_rates[col] = {"n_imputed": int(nulls), "pct": pct}
        if kind == "continuous":
            median = df[col].median()
            # If every value is NULL, fall back to 0 — flagged in report.
            df[col] = df[col].fillna(0 if pd.isna(median) else median)
        else:  # binary
            df[col] = df[col].fillna(0).astype(int)

    df.attrs["imputation_rates"] = imputation_rates
    return df
```

- [ ] **Step 4: Run the new tests**

Run: `.venv/bin/pytest tests/test_pipeline_analyze.py -v`

Expected: 11 tests PASS.

- [ ] **Step 5: Full suite**

Run: `.venv/bin/pytest -q`

Expected: **174 passing**.

- [ ] **Step 6: Commit**

```bash
git add pipeline/analyze.py tests/test_pipeline_analyze.py
git commit -m "feat(analyze): build_training_table with eligibility filters and imputation

Drops tax-exempt, PD-zoned, and condo-unit parcels (each for a documented
data-quality reason). Median-imputes continuous nulls and zero-imputes
binary nulls; imputation rates are attached to df.attrs so the report can
disclose them."
```

---

## Phase 3 — Distribution comparisons

### Task 5: `compare_distributions(df)` — per-signal stats for the report

**Files:**
- Modify: `pipeline/analyze.py`
- Modify: `tests/test_pipeline_analyze.py`

This produces a list of dicts the report writer renders as a table. For continuous: mean/median/std for positives vs negatives; for binary: positive-rate (% true) for positives vs negatives.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_pipeline_analyze.py`:

```python
def test_compare_distributions_continuous_and_binary(tmp_path):
    parcels = [
        _parcel_row(f"142100100{i:02d}0000",
                    lot_size_sf=4000.0 + i * 1000.0,
                    is_absentee=(1 if i % 2 == 0 else 0))
        for i in range(10)
    ]
    db_path = _build_analyze_db(tmp_path, parcels)
    positive_pins = {parcels[0]["pin"]: 2018, parcels[1]["pin"]: 2019}
    df = analyze.build_training_table(db_path, positive_pins)
    stats = analyze.compare_distributions(df)
    by_signal = {s["signal"]: s for s in stats}

    lot = by_signal["lot_size_sf"]
    assert lot["kind"] == "continuous"
    assert lot["n_positive"] == 2
    assert lot["n_negative"] == 8
    # Positives are the first two rows (4000, 5000) → mean 4500
    assert lot["positive_mean"] == 4500.0
    # Negatives are 5000–13000 step 1000 → mean 9000.0  (NOTE: pin 0 lot=4000 IS positive,
    # pin 1 lot=5000 IS positive — negatives are 6000..13000 → mean 9500)
    assert lot["negative_mean"] == 9500.0

    abs_ = by_signal["is_absentee"]
    assert abs_["kind"] == "binary"
    # Positives: pin 0 (i=0, abs=1), pin 1 (i=1, abs=0) → rate 0.5
    assert abs_["positive_rate"] == 0.5
    # Negatives: i in 2..9 → 4 absentee (even i: 2,4,6,8), 4 not → 0.5
    assert abs_["negative_rate"] == 0.5
```

- [ ] **Step 2: Run the failing test**

Run: `.venv/bin/pytest tests/test_pipeline_analyze.py::test_compare_distributions_continuous_and_binary -v`

Expected: FAIL with `AttributeError: ... 'compare_distributions'`.

- [ ] **Step 3: Implement `compare_distributions`**

Add to `pipeline/analyze.py`:

```python
def compare_distributions(df: pd.DataFrame) -> list[dict]:
    """Per-signal stats: continuous → mean/median/std; binary → positive rate.

    Returns a list of dicts (one per signal) so the report writer can render
    a single table without re-walking SIGNALS.
    """
    if df.empty:
        return []
    positives = df[df["label"] == 1]
    negatives = df[df["label"] == 0]
    out = []
    for col, kind, _src in SIGNALS:
        if kind == "continuous":
            out.append({
                "signal": col,
                "kind": "continuous",
                "n_positive": len(positives),
                "n_negative": len(negatives),
                "positive_mean":   round(float(positives[col].mean()), 4) if len(positives) else None,
                "negative_mean":   round(float(negatives[col].mean()), 4) if len(negatives) else None,
                "positive_median": round(float(positives[col].median()), 4) if len(positives) else None,
                "negative_median": round(float(negatives[col].median()), 4) if len(negatives) else None,
                "positive_std":    round(float(positives[col].std()), 4) if len(positives) > 1 else None,
                "negative_std":    round(float(negatives[col].std()), 4) if len(negatives) > 1 else None,
            })
        else:  # binary
            out.append({
                "signal": col,
                "kind": "binary",
                "n_positive": len(positives),
                "n_negative": len(negatives),
                "positive_rate": round(float(positives[col].mean()), 4) if len(positives) else None,
                "negative_rate": round(float(negatives[col].mean()), 4) if len(negatives) else None,
            })
    return out
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/test_pipeline_analyze.py -v`

Expected: 12 tests PASS.

- [ ] **Step 5: Full suite**

Run: `.venv/bin/pytest -q`

Expected: **175 passing**.

- [ ] **Step 6: Commit**

```bash
git add pipeline/analyze.py tests/test_pipeline_analyze.py
git commit -m "feat(analyze): compare_distributions — per-signal stats for the report

Continuous → mean/median/std for positives vs negatives. Binary → rate (%
true). One pass; output is the structure the markdown writer renders directly."
```

---

## Phase 4 — Logistic regression with bootstrap CIs

### Task 6: `fit_logistic_regression(df, n_bootstrap=200, random_state=0)`

**Files:**
- Modify: `pipeline/analyze.py`
- Modify: `tests/test_pipeline_analyze.py`

Returns one row per signal: `{signal, coef, ci_low, ci_high, significant, normalization_min, normalization_max}`. Mechanics:

- Z-score every continuous column by training-set mean/std so coefficients are comparable across very-different scales (lot_size_sf vs. far_gap_delta).
- Binary columns are fed in raw (already 0/1).
- Normalization range = (5th percentile, 95th percentile) of each continuous column on the training set; binary normalization is `(0, 1)`. These pass through to the YAML so the Score step (next plan) clips and rescales the same way.
- Class imbalance: `class_weight='balanced'` because positives are typically <10% of parcels.
- Bootstrap CI: 200 iterations, sampling rows with replacement, refit each, take the 2.5/97.5 percentiles per coefficient.
- "Significant" = bootstrap 95% CI does NOT cross 0.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_pipeline_analyze.py`:

```python
import numpy as np


def _build_separable_training_df(n_pos=40, n_neg=160, seed=0):
    """A synthetic dataframe where lot_size_sf strongly predicts label,
    is_llc weakly predicts, and cta_distance_ft is pure noise. Used to verify
    the regression actually picks out signal vs. noise."""
    rng = np.random.default_rng(seed)
    cols = [s[0] for s in analyze.SIGNALS]
    rows = []
    for label in [1] * n_pos + [0] * n_neg:
        row = {"pin": f"PIN{len(rows):05d}", "label": label}
        for col, kind, _ in analyze.SIGNALS:
            if col == "lot_size_sf":
                row[col] = rng.normal(8000 if label else 4000, 500)
            elif col == "is_llc":
                row[col] = int(rng.random() < (0.6 if label else 0.4))
            elif kind == "continuous":
                row[col] = rng.normal(0, 1)  # noise
            else:
                row[col] = int(rng.random() < 0.5)  # noise
        rows.append(row)
    df = pd.DataFrame(rows)[["pin", "label"] + cols]
    df.attrs["imputation_rates"] = {}
    return df


def test_fit_logistic_regression_picks_real_signal_over_noise():
    df = _build_separable_training_df()
    results = analyze.fit_logistic_regression(df, n_bootstrap=100, random_state=0)
    by_signal = {r["signal"]: r for r in results}

    # lot_size_sf is the dominant predictor and must be significant with a
    # positive coefficient.
    assert by_signal["lot_size_sf"]["significant"] is True
    assert by_signal["lot_size_sf"]["coef"] > 0

    # Pure-noise continuous columns must NOT be significant.
    assert by_signal["cta_distance_ft"]["significant"] is False

    # Normalization fields must be set per-kind.
    assert by_signal["lot_size_sf"]["normalization_min"] is not None
    assert by_signal["lot_size_sf"]["normalization_max"] is not None
    assert by_signal["is_llc"]["normalization_min"] == 0
    assert by_signal["is_llc"]["normalization_max"] == 1


def test_fit_logistic_regression_handles_zero_positives():
    df = _build_separable_training_df(n_pos=0, n_neg=20)
    results = analyze.fit_logistic_regression(df, n_bootstrap=10, random_state=0)
    # No positives → no model can be fit. Return one row per signal with
    # coef=0, significant=False so downstream code doesn't branch.
    assert len(results) == len(analyze.SIGNALS)
    for r in results:
        assert r["coef"] == 0.0
        assert r["significant"] is False
```

- [ ] **Step 2: Run the failing test**

Run: `.venv/bin/pytest tests/test_pipeline_analyze.py::test_fit_logistic_regression_picks_real_signal_over_noise -v`

Expected: FAIL with `AttributeError: ... 'fit_logistic_regression'`.

- [ ] **Step 3: Implement `fit_logistic_regression`**

Add to `pipeline/analyze.py`:

```python
import numpy as np
from sklearn.linear_model import LogisticRegression


def _zscore_continuous(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, tuple[float, float]]]:
    """Return a copy of df with continuous SIGNALS columns z-scored, plus the
    (mean, std) used for each column so the same transform can be re-applied
    to fresh data later if we ever want to."""
    out = df.copy()
    stats: dict[str, tuple[float, float]] = {}
    for col, kind, _ in SIGNALS:
        if kind != "continuous":
            continue
        mu = float(out[col].mean())
        sigma = float(out[col].std()) or 1.0  # avoid div-by-zero if column is constant
        out[col] = (out[col] - mu) / sigma
        stats[col] = (mu, sigma)
    return out, stats


def fit_logistic_regression(
    df: pd.DataFrame,
    *,
    n_bootstrap: int = 200,
    random_state: int = 0,
) -> list[dict]:
    """Fit a logistic regression on (features, label) and return one row per
    signal with coefficient, 95% bootstrap CI, significance flag, and the
    normalization range (5th–95th percentile for continuous, (0, 1) for binary)
    that the Score step uses to clip + rescale.

    Returns a stable order matching SIGNALS so callers can `dict`-zip if needed.
    """
    feature_cols = [s[0] for s in SIGNALS]
    if df.empty or df["label"].sum() == 0 or (df["label"] == 0).sum() == 0:
        # Can't fit a classifier with 0 positives or 0 negatives.
        return [
            {"signal": col, "kind": kind,
             "coef": 0.0, "ci_low": 0.0, "ci_high": 0.0, "significant": False,
             "normalization_min": 0.0 if kind == "binary" else None,
             "normalization_max": 1.0 if kind == "binary" else None}
            for col, kind, _ in SIGNALS
        ]

    z_df, _stats = _zscore_continuous(df)
    X = z_df[feature_cols].to_numpy(dtype=float)
    y = z_df["label"].to_numpy(dtype=int)

    base = LogisticRegression(class_weight="balanced", max_iter=1000, solver="liblinear")
    base.fit(X, y)
    base_coefs = base.coef_[0]  # shape (n_features,)

    rng = np.random.default_rng(random_state)
    n = len(z_df)
    boot = np.zeros((n_bootstrap, len(feature_cols)))
    for b in range(n_bootstrap):
        idx = rng.integers(0, n, size=n)
        Xb, yb = X[idx], y[idx]
        # If the bootstrap sample has only one class, skip that iteration.
        if len(np.unique(yb)) < 2:
            boot[b] = base_coefs
            continue
        m = LogisticRegression(class_weight="balanced", max_iter=1000, solver="liblinear")
        m.fit(Xb, yb)
        boot[b] = m.coef_[0]

    ci_low = np.percentile(boot, 2.5, axis=0)
    ci_high = np.percentile(boot, 97.5, axis=0)

    results = []
    for j, (col, kind, _) in enumerate(SIGNALS):
        if kind == "continuous":
            n_min = float(np.percentile(df[col].dropna(), 5))
            n_max = float(np.percentile(df[col].dropna(), 95))
        else:
            n_min, n_max = 0.0, 1.0
        results.append({
            "signal": col,
            "kind": kind,
            "coef": float(base_coefs[j]),
            "ci_low": float(ci_low[j]),
            "ci_high": float(ci_high[j]),
            # 95% CI doesn't cross 0 → significant.
            "significant": bool(ci_low[j] > 0 or ci_high[j] < 0),
            "normalization_min": n_min,
            "normalization_max": n_max,
        })
    return results
```

- [ ] **Step 4: Run the new tests**

Run: `.venv/bin/pytest tests/test_pipeline_analyze.py -v`

Expected: 14 tests PASS. The bootstrap test takes a few seconds (small N).

- [ ] **Step 5: Full suite**

Run: `.venv/bin/pytest -q`

Expected: **177 passing**.

- [ ] **Step 6: Commit**

```bash
git add pipeline/analyze.py tests/test_pipeline_analyze.py
git commit -m "feat(analyze): fit_logistic_regression with bootstrap CIs

sklearn LogisticRegression(class_weight='balanced') on z-scored continuous
features + raw binary features. 200-iteration bootstrap for 95% CIs (no
statsmodels dep). Returns per-signal coef, CI, significance flag, and
5th–95th percentile normalization range that the Score step will reuse."
```

---

## Phase 5 — Derive scoring weights

### Task 7: `derive_weights(regression_results)` — convert coefficients to YAML-ready entries

**Files:**
- Modify: `pipeline/analyze.py`
- Modify: `tests/test_pipeline_analyze.py`

Spec for the YAML-ready dict per signal:

```python
{
    "weight": 0.15,           # |coef| / sum(|coefs of significant signals|)
    "direction": "positive",  # sign of the coefficient
    "kind": "continuous",     # passes through to YAML for the Score step
    "normalization": {"min": ..., "max": ...},
    "insignificant": False,   # if True, weight is 0
}
```

Insignificant signals get `weight: 0` and `insignificant: true`. Magnitudes of significant signals normalize to sum=1.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_pipeline_analyze.py`:

```python
def test_derive_weights_normalizes_significant_only():
    results = [
        # Significant positive, large coef
        {"signal": "lot_size_sf", "kind": "continuous",
         "coef": 0.8, "ci_low": 0.5, "ci_high": 1.1, "significant": True,
         "normalization_min": 1500.0, "normalization_max": 12000.0},
        # Significant negative
        {"signal": "cta_distance_ft", "kind": "continuous",
         "coef": -0.4, "ci_low": -0.7, "ci_high": -0.1, "significant": True,
         "normalization_min": 200.0, "normalization_max": 5000.0},
        # Insignificant — gets weight 0
        {"signal": "is_llc", "kind": "binary",
         "coef": 0.05, "ci_low": -0.2, "ci_high": 0.3, "significant": False,
         "normalization_min": 0.0, "normalization_max": 1.0},
    ]
    weights = analyze.derive_weights(results)
    by_signal = {w["signal"]: w for w in weights}

    # Lot size has 2× the magnitude of cta_distance → 0.8 / 1.2 ≈ 0.667
    assert by_signal["lot_size_sf"]["weight"] == round(0.8 / 1.2, 4)
    assert by_signal["lot_size_sf"]["direction"] == "positive"

    assert by_signal["cta_distance_ft"]["weight"] == round(0.4 / 1.2, 4)
    assert by_signal["cta_distance_ft"]["direction"] == "negative"

    assert by_signal["is_llc"]["weight"] == 0.0
    assert by_signal["is_llc"]["insignificant"] is True

    # Significant weights must sum to 1.0 (modulo rounding).
    sig_sum = sum(w["weight"] for w in weights if not w["insignificant"])
    assert abs(sig_sum - 1.0) < 1e-3


def test_derive_weights_all_insignificant_returns_zero_weights():
    results = [
        {"signal": "lot_size_sf", "kind": "continuous",
         "coef": 0.05, "ci_low": -0.1, "ci_high": 0.2, "significant": False,
         "normalization_min": 0.0, "normalization_max": 1.0},
    ]
    weights = analyze.derive_weights(results)
    assert weights[0]["weight"] == 0.0
    assert weights[0]["insignificant"] is True
```

- [ ] **Step 2: Run the failing test**

Run: `.venv/bin/pytest tests/test_pipeline_analyze.py::test_derive_weights_normalizes_significant_only -v`

Expected: FAIL with `AttributeError: ... 'derive_weights'`.

- [ ] **Step 3: Implement `derive_weights`**

Add to `pipeline/analyze.py`:

```python
def derive_weights(regression_results: list[dict]) -> list[dict]:
    """Convert per-signal coefficients into YAML-ready scoring entries.

    Significant signals: weight = |coef| / sum(|coef| over significant signals).
    Insignificant signals: weight = 0, insignificant = True.

    Direction carries the sign so the Score step knows whether higher raw
    values push the score up or down.
    """
    sig_total = sum(abs(r["coef"]) for r in regression_results if r["significant"])
    out: list[dict] = []
    for r in regression_results:
        if not r["significant"] or sig_total == 0:
            weight = 0.0
            insig = True
        else:
            weight = round(abs(r["coef"]) / sig_total, 4)
            insig = False
        out.append({
            "signal": r["signal"],
            "kind": r["kind"],
            "weight": weight,
            "direction": "positive" if r["coef"] >= 0 else "negative",
            "normalization": {
                "min": r["normalization_min"],
                "max": r["normalization_max"],
            },
            "insignificant": insig,
            # Carry-through for the report
            "coef": r["coef"],
            "ci_low": r["ci_low"],
            "ci_high": r["ci_high"],
        })
    return out
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/test_pipeline_analyze.py -v`

Expected: 16 tests PASS.

- [ ] **Step 5: Full suite**

Run: `.venv/bin/pytest -q`

Expected: **179 passing**.

- [ ] **Step 6: Commit**

```bash
git add pipeline/analyze.py tests/test_pipeline_analyze.py
git commit -m "feat(analyze): derive_weights — coefficients to YAML-ready entries

Significant signals get weight = |coef| / sum(|coefs|), summing to 1.0.
Insignificant signals (95% CI crosses 0) get weight 0 + insignificant: true.
Direction carries the sign of the coefficient so the Score step knows whether
higher raw values are good or bad."
```

---

## Phase 6 — Output writers

### Task 8: `write_scoring_yaml(weights, version, top_n, path)`

**Files:**
- Modify: `pipeline/analyze.py`
- Modify: `tests/test_pipeline_analyze.py`

Format reflects the Score-step plan-to-come: one entry per signal with weight, direction, kind, normalization range, plus a top-level `version` and `top_n`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_pipeline_analyze.py`:

```python
import yaml


def test_write_scoring_yaml_roundtrip(tmp_path):
    weights = [
        {"signal": "lot_size_sf", "kind": "continuous",
         "weight": 0.6, "direction": "positive",
         "normalization": {"min": 1500.0, "max": 12000.0},
         "insignificant": False,
         "coef": 0.8, "ci_low": 0.5, "ci_high": 1.1},
        {"signal": "is_llc", "kind": "binary",
         "weight": 0.0, "direction": "positive",
         "normalization": {"min": 0.0, "max": 1.0},
         "insignificant": True,
         "coef": 0.05, "ci_low": -0.2, "ci_high": 0.3},
    ]
    out_path = tmp_path / "scoring.yaml"
    analyze.write_scoring_yaml(weights, version="1.0.0-test", top_n=20,
                               path=out_path)
    loaded = yaml.safe_load(out_path.read_text())
    assert loaded["version"] == "1.0.0-test"
    assert loaded["top_n"] == 20
    assert "generated_at" in loaded  # ISO-8601 string
    assert set(loaded["signals"].keys()) == {"lot_size_sf", "is_llc"}
    lot = loaded["signals"]["lot_size_sf"]
    assert lot["weight"] == 0.6
    assert lot["direction"] == "positive"
    assert lot["kind"] == "continuous"
    assert lot["normalization"] == {"min": 1500.0, "max": 12000.0}
    assert lot["insignificant"] is False
    assert loaded["signals"]["is_llc"]["insignificant"] is True
```

- [ ] **Step 2: Run the failing test**

Run: `.venv/bin/pytest tests/test_pipeline_analyze.py::test_write_scoring_yaml_roundtrip -v`

Expected: FAIL with `AttributeError: ... 'write_scoring_yaml'`.

- [ ] **Step 3: Implement `write_scoring_yaml`**

Add to `pipeline/analyze.py`:

```python
import yaml
from datetime import datetime, UTC


def write_scoring_yaml(
    weights: list[dict],
    *,
    version: str,
    top_n: int,
    path: Path,
) -> None:
    """Emit config/scoring.yaml in the format the Score step (next plan) reads.

    Top-level: version, generated_at, top_n, signals (mapping).
    Per-signal: weight, kind, direction, normalization {min, max}, insignificant.
    """
    payload = {
        "version": version,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "top_n": top_n,
        "signals": {
            w["signal"]: {
                "weight": w["weight"],
                "kind": w["kind"],
                "direction": w["direction"],
                "normalization": dict(w["normalization"]),
                "insignificant": w["insignificant"],
            }
            for w in weights
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False))
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/test_pipeline_analyze.py -v`

Expected: 17 tests PASS.

- [ ] **Step 5: Full suite**

Run: `.venv/bin/pytest -q`

Expected: **180 passing**.

- [ ] **Step 6: Commit**

```bash
git add pipeline/analyze.py tests/test_pipeline_analyze.py
git commit -m "feat(analyze): write_scoring_yaml emits the contract for the Score step

Top-level version + generated_at + top_n + per-signal {weight, kind, direction,
normalization {min,max}, insignificant}. The Score plan (next milestone) reads
this back without having to know how the weights were derived."
```

---

### Task 9: `write_analysis_report(...)` — markdown report

**Files:**
- Modify: `pipeline/analyze.py`
- Modify: `tests/test_pipeline_analyze.py`

The report is the human-readable companion to `scoring.yaml`. Structure:

1. Header (date, DB path, geo name, sample sizes).
2. Eligibility funnel (parcels in geo → after exempt filter → after PD/condo/no-zone filter).
3. Imputation rates table.
4. Per-signal distribution comparison table.
5. Logistic regression results table (coef, CI, significant?, derived weight, direction).
6. Caveats section (snapshot fidelity, missing-data signals, condo/commercial gap).
7. Top 5 signals by weight magnitude.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_pipeline_analyze.py`:

```python
def test_write_analysis_report_contains_required_sections(tmp_path):
    funnel = {"total_parcels": 67677, "after_exempt_drop": 67000,
              "after_no_zone_drop": 66800, "after_pd_drop": 64781,
              "after_condo_unit_drop": 17753}
    distributions = [
        {"signal": "lot_size_sf", "kind": "continuous",
         "n_positive": 120, "n_negative": 17633,
         "positive_mean": 9500.0, "negative_mean": 4200.0,
         "positive_median": 8800.0, "negative_median": 3500.0,
         "positive_std": 4000.0, "negative_std": 2200.0},
        {"signal": "is_llc", "kind": "binary",
         "n_positive": 120, "n_negative": 17633,
         "positive_rate": 0.65, "negative_rate": 0.14},
    ]
    weights = [
        {"signal": "lot_size_sf", "kind": "continuous",
         "weight": 0.6, "direction": "positive",
         "normalization": {"min": 1500.0, "max": 12000.0},
         "insignificant": False,
         "coef": 0.8, "ci_low": 0.5, "ci_high": 1.1},
        {"signal": "is_llc", "kind": "binary",
         "weight": 0.4, "direction": "positive",
         "normalization": {"min": 0.0, "max": 1.0},
         "insignificant": False,
         "coef": 0.5, "ci_low": 0.2, "ci_high": 0.8},
    ]
    imputation = {"lot_size_sf": {"n_imputed": 0, "pct": 0.0},
                  "is_llc":      {"n_imputed": 0, "pct": 0.0}}
    out_path = tmp_path / "report.md"
    analyze.write_analysis_report(
        path=out_path,
        db_path=Path("data/full.db"),
        geo_name="Lincoln Park / Lakeview",
        n_positive=120,
        funnel=funnel,
        imputation=imputation,
        distributions=distributions,
        weights=weights,
        version="1.0.0-test",
    )
    body = out_path.read_text()
    # Header
    assert "# Initial Scoring Weights" in body
    assert "Lincoln Park / Lakeview" in body
    assert "data/full.db" in body
    assert "1.0.0-test" in body
    # Funnel mentions every step
    assert "67,677" in body
    assert "17,753" in body
    # Distribution table — at least the column headers and a row
    assert "lot_size_sf" in body
    assert "is_llc" in body
    # Regression results
    assert "0.5" in body and "1.1" in body  # CI bounds
    # Top-5 section
    assert "Top 5 signals by weight" in body
    # Caveats
    assert "Caveats" in body
    assert "tax_delinquent" in body  # the missing signal must be called out
    assert "snapshot" in body.lower()  # snapshot-fidelity caveat
```

- [ ] **Step 2: Run the failing test**

Run: `.venv/bin/pytest tests/test_pipeline_analyze.py::test_write_analysis_report_contains_required_sections -v`

Expected: FAIL with `AttributeError: ... 'write_analysis_report'`.

- [ ] **Step 3: Implement `write_analysis_report`**

Add to `pipeline/analyze.py`:

```python
def write_analysis_report(
    *,
    path: Path,
    db_path: Path,
    geo_name: str,
    n_positive: int,
    funnel: dict,
    imputation: dict,
    distributions: list[dict],
    weights: list[dict],
    version: str,
) -> None:
    """Emit the markdown analysis report. Everything in the report is derived
    from the args — no DB access here, so the report writer is testable in
    isolation."""
    lines: list[str] = []
    a = lines.append
    a(f"# Initial Scoring Weights — {geo_name}")
    a("")
    a(f"- **Version:** `{version}`")
    a(f"- **Generated at:** {datetime.now(UTC).isoformat(timespec='seconds')}")
    a(f"- **DB:** `{db_path}`")
    a(f"- **Positive examples (qualifying permits 2006-present):** {n_positive:,}")
    a("")

    # Funnel
    a("## Eligibility funnel")
    a("")
    a("| Step | Parcels remaining |")
    a("|---|---|")
    a(f"| Total parcels in DB | {funnel['total_parcels']:,} |")
    a(f"| After dropping tax-exempt | {funnel['after_exempt_drop']:,} |")
    a(f"| After dropping no-zone-class | {funnel['after_no_zone_drop']:,} |")
    a(f"| After dropping PD-zoned | {funnel['after_pd_drop']:,} |")
    a(f"| After dropping condo units | **{funnel['after_condo_unit_drop']:,}** (training set) |")
    a("")

    # Imputation rates
    a("## Imputation rates")
    a("")
    a("Continuous NULLs imputed with the training-set median; binary NULLs imputed with 0.")
    a("")
    a("| Signal | n imputed | % of training set |")
    a("|---|---|---|")
    for sig, rate in imputation.items():
        a(f"| {sig} | {rate['n_imputed']:,} | {rate['pct']}% |")
    a("")

    # Distribution comparisons
    a("## Per-signal distribution: positive vs. negative")
    a("")
    a("| Signal | Kind | n+ | n- | Pos mean | Neg mean | Pos med | Neg med | Pos rate | Neg rate |")
    a("|---|---|---|---|---|---|---|---|---|---|")
    for d in distributions:
        if d["kind"] == "continuous":
            a(f"| {d['signal']} | continuous | {d['n_positive']:,} | {d['n_negative']:,} | "
              f"{d['positive_mean']} | {d['negative_mean']} | "
              f"{d['positive_median']} | {d['negative_median']} | — | — |")
        else:
            a(f"| {d['signal']} | binary | {d['n_positive']:,} | {d['n_negative']:,} | "
              f"— | — | — | — | {d['positive_rate']} | {d['negative_rate']} |")
    a("")

    # Regression results
    a("## Logistic regression results")
    a("")
    a("Continuous features are z-scored before fitting so coefficients are comparable. "
      "95% CIs are bootstrap (200 iterations, sample-with-replacement). "
      "A signal is **significant** when its 95% CI does not cross 0; insignificant "
      "signals get weight 0 and are not used in the score.")
    a("")
    a("| Signal | Coef | 95% CI | Significant | Direction | Weight |")
    a("|---|---|---|---|---|---|")
    for w in weights:
        ci = f"[{w['ci_low']:.3f}, {w['ci_high']:.3f}]"
        sig = "yes" if not w["insignificant"] else "**no**"
        a(f"| {w['signal']} | {w['coef']:.3f} | {ci} | {sig} | {w['direction']} | "
          f"{w['weight']:.3f} |")
    a("")

    # Top 5 by weight
    significant = [w for w in weights if not w["insignificant"]]
    significant.sort(key=lambda x: x["weight"], reverse=True)
    a("## Top 5 signals by weight magnitude")
    a("")
    if not significant:
        a("_No signals reached significance — see Caveats._")
    else:
        for i, w in enumerate(significant[:5], 1):
            a(f"{i}. **{w['signal']}** — weight {w['weight']:.3f}, direction {w['direction']}")
    a("")

    # Caveats
    a("## Caveats")
    a("")
    a("- **Snapshot fidelity:** v1 uses the *current* parcels table for all features, "
      "not a per-PIN reconstructed pre-development snapshot. Most signals (zoning class, "
      "lot_size_sf, cta_distance_ft, is_llc) don't change materially year-to-year; signals "
      "that do (hold_duration_years, assessed-value trends) are biased toward the post-event "
      "state. Document the bias direction; refine in a future iteration if a signal's "
      "weight looks suspiciously high.")
    a("- **`tax_delinquent` excluded entirely:** the Cook County Clerk delinquent-tax "
      "CSV referenced in the data-sources spec is a header-only stub on `data/full.db` "
      "(see `docs/analysis/2026-04-27-data-source-audit.md` §1). The strongest "
      "motivation-to-sell signal in the literature is missing. The model will under-weight "
      "motivation as a result; decide on access path (targeted scrape vs FOIA) before "
      "re-running.")
    a("- **`has_vacancy_report` excluded:** the configured 311 dataset (`7nii-7srd`) is "
      "a defunct legacy feed that ends in 2018; switching to `vauj-4grr` is on the audit's "
      "Tier-1 do-list.")
    a("- **Condo + commercial building data gap:** `building_sf`, `year_built`, `condition`, "
      "`built_far` are excluded from features because ~78% of all parcels (and ~35% of the "
      "non-condo-unit subset) lack values. This will improve once the building-footprints "
      "merge from the audit branch is run against the live DB.")
    a("- **`open_violations_count` and `years_since_last_permit` are sparse:** the "
      "address-first matcher fix shipped in this branch hasn't been re-run on `data/full.db` "
      "yet at training time. Re-run those two fetches and re-run analyze for tighter CIs.")
    a("- **`appeal_count` is too coarse:** ~80% of parcels show ≥1 lifetime appeal. The "
      "audit recommends windowing this to last-3-years; v1 uses lifetime as-is.")
    a("- **`is_absentee` is over-firing on condo buildings:** 54.5% true population-wide. "
      "The condo-unit drop in the eligibility funnel removes most of the false positives, "
      "but the building-rep PINs (mailed to property managers) likely still over-fire.")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines))
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/test_pipeline_analyze.py -v`

Expected: 18 tests PASS.

- [ ] **Step 5: Full suite**

Run: `.venv/bin/pytest -q`

Expected: **181 passing**.

- [ ] **Step 6: Commit**

```bash
git add pipeline/analyze.py tests/test_pipeline_analyze.py
git commit -m "feat(analyze): write_analysis_report markdown writer

Renders the eligibility funnel, imputation rates, per-signal distributions,
regression coefficients with CIs, top-5-by-weight, and a caveats section
calling out tax_delinquent, the condo/commercial building-data gap, the
permit/violation match-rate caveat, and snapshot-fidelity. Writer takes
plain dicts so it is fully testable without a live DB."
```

---

## Phase 7 — Orchestrator + CLI

### Task 10: Wire `analyze(db_path, geo, scoring_yaml_path, report_md_path)` end-to-end

**Files:**
- Modify: `pipeline/analyze.py`
- Modify: `tests/test_pipeline_analyze.py`

The orchestrator: identify positives → build training table → compare distributions → fit regression → derive weights → write yaml → write report. Plus it owns the funnel-stat collection (build_training_table doesn't currently track funnel — we'll have it return the counts as `df.attrs['funnel']`).

- [ ] **Step 1: Extend `build_training_table` to track the funnel**

Modify the function in `pipeline/analyze.py`. After the `eligible = []` loop, add:

```python
    # Track the eligibility funnel so the report can show it.
    funnel = {
        "total_parcels": len(rows),
        "after_exempt_drop": sum(1 for r in rows if r["pin"] not in exempt_pins),
        "after_no_zone_drop": sum(1 for r in rows
                                  if r["pin"] not in exempt_pins and r["zone_class"]),
        "after_pd_drop": sum(1 for r in rows
                             if r["pin"] not in exempt_pins and r["zone_class"]
                             and not _is_pd_zone(r["zone_class"])),
        "after_condo_unit_drop": len(eligible),
    }
```

…and at the bottom (before `return df`) add `df.attrs["funnel"] = funnel`. Also pass funnel through when df is empty (return early branch).

Update the existing `test_build_training_table_basic_shape` to assert:

```python
    assert df.attrs["funnel"]["total_parcels"] == 2
    assert df.attrs["funnel"]["after_condo_unit_drop"] == 2
```

Run: `.venv/bin/pytest tests/test_pipeline_analyze.py -v` — must stay green.

- [ ] **Step 2: Write the orchestrator integration test**

Append to `tests/test_pipeline_analyze.py`:

```python
def test_analyze_end_to_end_writes_yaml_and_report(tmp_path):
    """Smoke test: run the full orchestrator on a tiny synthetic DB and confirm
    both output files are written with the expected top-level shape."""
    parcels = []
    permits = []
    # 5 positives — large lots, LLC owners, longer hold
    for i in range(5):
        pin = f"14210010{i:03d}0000"
        parcels.append(_parcel_row(pin, lot_size_sf=8000.0 + i * 200,
                                   hold_duration_years=20.0,
                                   is_llc=1,
                                   address=f"{100 + i} W FAKE ST",
                                   lat=41.93 + i * 0.0001,
                                   lng=-87.65 + i * 0.0001))
        permits.append({
            "permit_number": f"perm-{i}",
            "permit_type": "PERMIT - NEW CONSTRUCTION",
            "issue_date": "2018-05-12",
            "street_number": str(100 + i), "street_direction": "W",
            "street_name": "FAKE ST",
            "latitude": 41.93 + i * 0.0001, "longitude": -87.65 + i * 0.0001,
        })
    # 25 negatives — smaller lots, mostly individual owners, shorter hold
    for i in range(25):
        pin = f"14210020{i:03d}0000"
        parcels.append(_parcel_row(pin, lot_size_sf=3500.0 + i * 50,
                                   hold_duration_years=4.0,
                                   is_llc=0,
                                   address=f"{200 + i} W OTHER ST",
                                   lat=41.94 + i * 0.0001,
                                   lng=-87.66 + i * 0.0001))
    db_path = _build_analyze_db(tmp_path, parcels, permits)
    geo = type("G", (), {"name": "Test Geography"})()  # duck-typed GeographyConfig
    scoring_yaml = tmp_path / "scoring.yaml"
    report_md = tmp_path / "report.md"

    analyze.analyze(db_path=db_path, geo=geo,
                    scoring_yaml_path=scoring_yaml, report_md_path=report_md)

    # YAML
    assert scoring_yaml.exists()
    loaded = yaml.safe_load(scoring_yaml.read_text())
    assert "version" in loaded
    assert "generated_at" in loaded
    assert "signals" in loaded
    # Every SIGNAL must appear in the YAML, even insignificant ones.
    for col, _kind, _src in analyze.SIGNALS:
        assert col in loaded["signals"]

    # Report
    assert report_md.exists()
    body = report_md.read_text()
    assert "Test Geography" in body
    assert "Initial Scoring Weights" in body
    assert "Caveats" in body
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `.venv/bin/pytest tests/test_pipeline_analyze.py::test_analyze_end_to_end_writes_yaml_and_report -v`

Expected: FAIL with `NotImplementedError: Implemented in Task 10`.

- [ ] **Step 4: Implement the orchestrator**

Replace the stub `analyze()` in `pipeline/analyze.py`:

```python
SCORING_VERSION_PREFIX = "1.0.0"


def analyze(
    db_path: Path,
    geo: GeographyConfig,
    scoring_yaml_path: Path,
    report_md_path: Path,
) -> None:
    """Orchestrate: positives → training set → distributions → regression →
    weights → write yaml + report."""
    positives = identify_positive_examples(db_path)
    df = build_training_table(db_path, positives)
    distributions = compare_distributions(df)
    regression = fit_logistic_regression(df)
    weights = derive_weights(regression)

    today = datetime.now(UTC).strftime("%Y-%m-%d")
    version = f"{SCORING_VERSION_PREFIX}-{today}"
    write_scoring_yaml(weights, version=version, top_n=20, path=scoring_yaml_path)
    write_analysis_report(
        path=report_md_path,
        db_path=db_path,
        geo_name=getattr(geo, "name", "unknown"),
        n_positive=len(positives),
        funnel=df.attrs.get("funnel", {}),
        imputation=df.attrs.get("imputation_rates", {}),
        distributions=distributions,
        weights=weights,
        version=version,
    )
```

- [ ] **Step 5: Run the new test**

Run: `.venv/bin/pytest tests/test_pipeline_analyze.py -v`

Expected: 19 tests PASS.

- [ ] **Step 6: Full suite**

Run: `.venv/bin/pytest -q`

Expected: **182 passing**.

- [ ] **Step 7: Commit**

```bash
git add pipeline/analyze.py tests/test_pipeline_analyze.py
git commit -m "feat(analyze): orchestrate analyze() end-to-end + funnel stats

build_training_table now attaches df.attrs['funnel'] with the eligibility
counts. analyze() wires identify_positive_examples → build_training_table →
compare_distributions → fit_logistic_regression → derive_weights → writers.
Version stamp = 1.0.0-YYYY-MM-DD."
```

---

### Task 11: CLI entry point — `python -m pipeline.analyze --db ... --config-dir ...`

**Files:**
- Modify: `pipeline/analyze.py`
- Modify: `tests/test_pipeline_analyze.py`

So the analysis can be re-run from the shell against the real DB.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_pipeline_analyze.py`:

```python
import subprocess
import sys


def test_cli_runs_analyze_against_synthetic_db(tmp_path):
    parcels = [_parcel_row(f"14210010{i:03d}0000",
                            lot_size_sf=4000.0 + i * 500,
                            address=f"{100+i} W FAKE ST",
                            lat=41.93 + i*0.0001, lng=-87.65 + i*0.0001)
               for i in range(8)]
    permits = [{"permit_number": "p1",
                "permit_type": "PERMIT - NEW CONSTRUCTION",
                "issue_date": "2019-01-01",
                "street_number": "100", "street_direction": "W",
                "street_name": "FAKE ST",
                "latitude": 41.93, "longitude": -87.65}]
    db_path = _build_analyze_db(tmp_path, parcels, permits)
    # Minimal config dir with just geography.yaml
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "geography.yaml").write_text("""
name: Test
polygon:
  - [41.92, -87.69]
  - [41.92, -87.62]
  - [41.95, -87.62]
  - [41.95, -87.69]
bbox:
  min_lat: 41.92
  max_lat: 41.95
  min_lng: -87.69
  max_lng: -87.62
""".strip())
    scoring = tmp_path / "scoring.yaml"
    report = tmp_path / "report.md"

    result = subprocess.run([
        sys.executable, "-m", "pipeline.analyze",
        "--db", str(db_path),
        "--config-dir", str(config_dir),
        "--scoring-yaml", str(scoring),
        "--report-md", str(report),
    ], capture_output=True, text=True, cwd=str(Path(__file__).resolve().parent.parent))
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert scoring.exists()
    assert report.exists()
```

- [ ] **Step 2: Run the failing test**

Run: `.venv/bin/pytest tests/test_pipeline_analyze.py::test_cli_runs_analyze_against_synthetic_db -v`

Expected: FAIL — `python -m pipeline.analyze` is not yet runnable.

- [ ] **Step 3: Add the CLI block**

Append to `pipeline/analyze.py`:

```python
def _cli(argv: list[str] | None = None) -> int:
    import argparse
    from pipeline.config import get_geography

    p = argparse.ArgumentParser(prog="pipeline.analyze",
                                description="Derive initial scoring weights from permit history.")
    p.add_argument("--db", required=True, type=Path,
                   help="Path to the SQLite DB (e.g. data/full.db).")
    p.add_argument("--config-dir", required=True, type=Path,
                   help="Directory containing geography.yaml.")
    p.add_argument("--scoring-yaml", required=True, type=Path,
                   help="Output path for config/scoring.yaml.")
    p.add_argument("--report-md", required=True, type=Path,
                   help="Output path for the markdown analysis report.")
    args = p.parse_args(argv)

    geo = get_geography(args.config_dir)
    analyze(db_path=args.db, geo=geo,
            scoring_yaml_path=args.scoring_yaml,
            report_md_path=args.report_md)
    print(f"Wrote {args.scoring_yaml}")
    print(f"Wrote {args.report_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
```

- [ ] **Step 4: Run the test**

Run: `.venv/bin/pytest tests/test_pipeline_analyze.py::test_cli_runs_analyze_against_synthetic_db -v`

Expected: PASS.

- [ ] **Step 5: Full suite**

Run: `.venv/bin/pytest -q`

Expected: **183 passing**.

- [ ] **Step 6: Commit**

```bash
git add pipeline/analyze.py tests/test_pipeline_analyze.py
git commit -m "feat(analyze): CLI entry point — python -m pipeline.analyze

argparse-driven: --db, --config-dir, --scoring-yaml, --report-md. Lets the
analysis be re-run from the shell against any DB whenever weights need to
be refreshed."
```

---

## Phase 8 — Run against the live DB and ship the artifacts

### Task 12: Run against `data/full.db`, capture deliverables, write summary

**Files:**
- Modify: `config/scoring.yaml` (created)
- Modify: `docs/analysis/2026-04-27-initial-scoring-weights.md` (created)

This is the one-time run that produces the actual deliverables. No code changes. The CI test already ran on synthetic data; this step captures the real-world artifact.

- [ ] **Step 1: Confirm the live DB exists and is healthy**

Run: `ls -lh data/full.db`

Expected: file is present, ~628 MB.

Run: `.venv/bin/python -c "import sqlite3; c=sqlite3.connect('data/full.db'); print(c.execute('SELECT COUNT(*) FROM parcels').fetchone()[0])"`

Expected: ~67,677.

- [ ] **Step 2: Run the analyzer against the live DB**

Run from `chicago-pipeline/`:

```bash
.venv/bin/python -m pipeline.analyze \
    --db data/full.db \
    --config-dir config \
    --scoring-yaml config/scoring.yaml \
    --report-md docs/analysis/2026-04-27-initial-scoring-weights.md
```

Expected: exits 0, prints `Wrote config/scoring.yaml` and the report path. Wall time should be under 60s — the bootstrap is the slow part, ~200 fits on ~17k rows.

If it errors, do NOT proceed to Step 3. Investigate and fix in the relevant Phase 1-7 task. (Likely failure modes: a column we depend on is unexpectedly NULL on the real DB → median imputation should mask it, but if 100% NULL the median itself is NaN → check the `if every value is NULL, fall back to 0` branch in `build_training_table`. If the bootstrap is unreasonably slow, lower `n_bootstrap` for the live run only by passing it via a CLI flag in a follow-up commit — don't lower the test default.)

- [ ] **Step 3: Sanity-check the YAML**

Run: `head -40 config/scoring.yaml`

Expected: top-level `version`, `generated_at`, `top_n: 20`, then `signals:` with one entry per row in `analyze.SIGNALS`. Each signal entry has `weight`, `kind`, `direction`, `normalization`, `insignificant`. No NaN strings; no missing fields.

Spot-check that significant weights sum to ~1.0:

```bash
.venv/bin/python -c "import yaml; d=yaml.safe_load(open('config/scoring.yaml')); s=sum(v['weight'] for v in d['signals'].values() if not v['insignificant']); print(f'sum of significant weights: {s:.4f}')"
```

Expected: very close to 1.0 (rounding to 4 decimals can drift a hair).

- [ ] **Step 4: Read the markdown report end-to-end**

Open `docs/analysis/2026-04-27-initial-scoring-weights.md` and read it from top to bottom. Confirm:

- The eligibility funnel makes sense (e.g., a couple-thousand drop for tax-exempt, a couple-thousand drop for PD, a big drop for condo units leaving ~17–20k training rows).
- The per-signal distribution table reads sensibly — positive lots should be larger than negative lots, etc.
- The regression-results table has CIs for every signal.
- The top-5-by-weight section has 5 entries (or fewer if fewer than 5 are significant).
- The caveats section is intact and references the audit doc.
- No "None" or "nan" strings in the body (median fallback should have caught those — investigate any that appear).

- [ ] **Step 5: Commit the deliverables**

```bash
git add config/scoring.yaml docs/analysis/2026-04-27-initial-scoring-weights.md
git commit -m "feat(analyze): land initial scoring.yaml + analysis report against data/full.db

Run output of pipeline.analyze on data/full.db. config/scoring.yaml carries
weights, normalization ranges, and a top_n=20 default for the Score step.
docs/analysis/2026-04-27-initial-scoring-weights.md documents the funnel,
per-signal distributions, regression coefficients with bootstrap CIs, and
caveats — including the tax_delinquent / vacancy / condo-data gaps from
the 2026-04-27 audit."
```

- [ ] **Step 6: Report final summary back**

Per the user's "what 'done' looks like" criteria, copy these into the chat as the closing summary:

1. Number of positive examples found (`n_positive` from the report).
2. Top 5 signals by weight magnitude (from the report's "Top 5" section).
3. Any signals that came back statistically insignificant and how they were handled (the YAML entries with `insignificant: true` got `weight: 0`).

---

## Self-review

Re-read the spec and check coverage before handing the plan off:

- [x] **Scoring is a weighted sum (continuous normalized to 0–1, binary as 0/1):** scoring.yaml's `kind` + `normalization {min,max}` carries everything the Score step needs. Score step is its own plan; this plan provides the contract.
- [x] **Initial weights from historical analysis:** Tasks 6-8 land the regression-derived weights.
- [x] **Positive = NEW CONSTRUCTION + WRECKING/DEMOLITION 2006-present:** Task 3, `_is_qualifying_permit`.
- [x] **Negative = all other parcels in the same geography/period:** Task 4, `build_training_table`. Period filtering is implicit in the geography of the DB.
- [x] **Pre-dev chars = parcel snapshot from year before the permit:** v1 uses *current* state, with the limitation documented in the report's Caveats. The spec is a target; v1 ships pragmatic.
- [x] **Compare distributions:** Task 5.
- [x] **Logistic regression for weight coefficients:** Task 6.
- [x] **Report sample sizes + confidence intervals:** Task 6 (CIs) + Task 9 (report).
- [x] **Output: config/scoring.yaml + markdown analysis report:** Tasks 8, 9, 12.
- [x] **Versioned config/scoring.yaml so each scoring run records weights it used:** Task 8 + Task 10 (`SCORING_VERSION_PREFIX-YYYY-MM-DD`).
- [x] **Don't break tests:** every task ends with `.venv/bin/pytest -q` checkpoint.
- [x] **Use TDD:** every task is test-first.
- [x] **Use sklearn:** Task 6.
- [x] **Use fixture-driven tests, not the live DB, for analysis logic:** every test in Tasks 2-11 builds an isolated synthetic DB via `_build_analyze_db`. Only Task 12 touches `data/full.db`.
- [x] **Tax_delinquent excluded:** Task 2 (SIGNALS list) + Task 9 (caveat).
- [x] **PD parcels dropped:** Task 4.
- [x] **Score / score_version columns left empty:** This plan does not write them — the Score plan does.
- [x] **Deliverable paths:** `config/scoring.yaml` and `docs/analysis/2026-04-27-initial-scoring-weights.md`.

No placeholders, no "TBD", every code block self-contained.

---

## Score Plan Scope (next milestone — write a separate plan after this lands)

Once this plan ships, the next plan should cover:

1. **`pipeline/score.py`** that reads `config/scoring.yaml`, walks the `parcels` table, and writes `score` + `score_version` per row.
2. **Per-signal scoring math:** for continuous, clip to `(normalization.min, normalization.max)`, scale to [0, 1], multiply by `weight`, flip sign if `direction: negative`. For binary, value × weight, flipped if negative.
3. **Aggregation:** weighted sum across signals, then renormalize the signed sum to a 0–100 output. Decide whether to renormalize per-run (max observed score → 100) or use a fixed scale (sum-of-weights = 1.0 → 100). Spec leans toward the latter; bias note: insignificant signals don't contribute, so the achievable max is < 100 if any signals are insignificant unless we re-normalize over only the active weight mass.
4. **Consolidation-group scoring:** also score the `consolidation_groups` table using its combined fields. Spec calls this out (Section 2 of the master design).
5. **CLI:** `python -m pipeline.score --db data/full.db --config-dir config`.
6. **Re-runnability:** scoring should be deterministic given the same DB + same scoring.yaml; running it twice must produce identical scores.
7. **Top-N integration:** the UI already filters by score; this plan just needs the column populated. No UI changes.

Reference: spec Section 2 (Score), spec Section 4 (Review UI score breakdown), data-sources spec §"Pipeline Architecture".
