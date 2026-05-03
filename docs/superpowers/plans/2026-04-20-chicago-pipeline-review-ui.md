# Chicago Pipeline Review UI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a read-only Flask web UI at `localhost:5000` that renders the three-column Review UI from the mockup (ranked list · map · detail panel) over the existing `smoke.db` schema, with a config-driven filter panel. Scoring and outreach are out of scope for this plan.

**Architecture:** Flask app (`webapp/` package) serves a single HTML shell + JSON APIs. Filter panel, ranked list, map, and detail panel are rendered client-side from API responses. Filter controls are auto-generated from `config/ui_filters.yaml` plus live SQLite column-type introspection. No authentication, no writes, no background jobs — the UI reads SQLite and renders. Score breakdown component is a stub labelled "Scoring not yet available"; outreach sequence, feedback report, and action buttons are hidden behind a feature flag defaulting to off.

**Tech Stack:** Flask 3, Jinja2, vanilla JS (no build step), Leaflet 1.9.4 + CARTO dark tiles (CDN, same as mockup), SQLite via stdlib `sqlite3`, PyYAML (already in requirements), pytest + Flask test client.

---

## Risks & Gaps (spec vs. current state)

Flagging before tasks begin — these inform scope decisions baked into the plan.

1. **`score` column is NULL for all 641 parcels.** The mockup's left-panel sort is "by score desc" and the right-panel "Score Breakdown" is a hero component. Per the user, scoring is deferred. **Mitigation:** Sort defaults to `last_updated_date DESC, hold_duration_years DESC NULLS LAST`. The "Score Breakdown" section is a stub card reading "Scoring not yet available — run `pipeline.score` to populate." Top-bar meta shows `Score vN/a`.

2. **Most derived columns on `parcels` are unpopulated in smoke.db.** The schema reserves columns `max_far`, `built_far`, `far_gap`, `estimated_annual_tax`, `tax_increase_pct_1yr/5yr`, `cta_nearest_station`, `cta_distance_ft`, `land_building_ratio`, `year_built`, `lot_size_sf`, `building_sf`, `condition`, `building_classification`, `zone_class`, `assessed_total`, `open_violations_count`, `oldest_violation_age_days`, `appeal_count`, `has_vacancy_report`, `years_since_last_permit`, `tax_delinquent`, `delinquency_years` — all populated as part of the not-yet-built "score" pipeline stage. In smoke.db, `zone_class`, `assessed_total` (73/641), `year_built`, `lot_size_sf`, `building_sf`, FAR fields, CTA fields, violation counts, and tax deltas are mostly NULL. **Mitigation:** UI renders NULLs as em-dash ("—") everywhere; filter controls treat NULLs as "not matching" range/dropdown filters and include them in unfiltered results. Do not fabricate values.

3. **`raw_cdp_zoning` is empty in smoke.db (0 rows).** Zoning spatial join hasn't run for the smoke area. **Mitigation:** Zone-class dropdown is still rendered from distinct values (will show only NULL/empty); UI accepts this gracefully. No errors if the dropdown is effectively empty.

4. **`listing_status` column exists but is never populated (Plan 4).** The mockup shows orange "Listed" pins. **Mitigation:** Map layer for "Listed" is defined but empty in this plan; the toggle works but shows no pins. No stub listing data.

5. **Contacts / registered agent / phone / email** are Plan 4 (enrichment). Mockup Owner section shows these. **Mitigation:** Render fields from `contacts` if any rows exist (none will in smoke.db); otherwise render em-dashes. No stub contact data.

6. **Outreach sequence timeline, Due Today banner, Feedback Report, Actions panel** all depend on `outreach` / `waves` tables that are empty. **Mitigation:** All four are rendered behind a single `FEATURE_OUTREACH` flag in `webapp/app.py` defaulting to `False`. The top-bar "Due Today" banner and the right-panel sections below "Financials" are omitted entirely when the flag is off. This keeps the UI clean and the templates forward-compatible when outreach work begins.

7. **Consolidation display.** 68 consolidation_groups exist and 68 parcels carry a `consolidation_group_id`. The mockup shows purple "Consolidated" map pins and treats groups as separate entities in the list. **In scope:** Purple map pins for parcels where `consolidation_group_id IS NOT NULL`. A "Consolidated" tag on list items. **Not in scope:** Rendering consolidation groups as first-class list rows (the spec says groups are scored as a single entity, which requires scoring).

8. **Performance / map pin volume.** 641 parcels fits comfortably in one GeoJSON payload. No clustering is needed for smoke.db. When scaling to ~25k parcels the UI will need MarkerCluster; flag for later, not this plan.

---

## File Structure

**New files:**

```
chicago-pipeline/
  webapp/
    __init__.py                # package marker + create_app re-export
    __main__.py                # `python -m webapp` CLI entrypoint
    app.py                     # create_app(db_path, feature_outreach=False)
    routes.py                  # all HTTP route handlers (small enough to keep in one file)
    filter_schema.py           # load ui_filters.yaml, introspect DB, emit filter schema JSON
    parcel_query.py            # filters dict -> parameterized SQL WHERE + ORDER BY
    templates/
      index.html               # the three-column shell
    static/
      css/style.css            # extracted from mockup
      js/filters.js            # filter panel render + state
      js/list.js               # ranked list render
      js/map.js                # Leaflet init + pin render
      js/detail.js             # right panel render
      js/app.js                # app bootstrap + selection state
  config/
    ui_filters.yaml            # filter panel config (spec Section 4)
  tests/
    test_webapp_filter_schema.py
    test_webapp_parcel_query.py
    test_webapp_routes.py
```

**Modified files:**

- `chicago-pipeline/requirements.txt` — add `flask==3.0.3`
- `chicago-pipeline/tests/conftest.py` — add `populated_db_path` fixture (copy of smoke.db into tmp_path, or a hand-built minimal set)

**Responsibilities (one per file):**

- `webapp/app.py` — Flask app factory; holds DB path + feature flags on `app.config`.
- `webapp/routes.py` — URL → handler. Each handler is thin, delegates to `filter_schema` / `parcel_query`.
- `webapp/filter_schema.py` — pure logic: YAML + sqlite schema → JSON-serializable filter schema. No Flask imports.
- `webapp/parcel_query.py` — pure logic: filter dict → `(sql, params)` tuple. No Flask imports.
- Frontend JS modules each own one panel; `app.js` is the only module that wires selection state across them.

---

## Task 1: Scaffolding — Flask app factory + test harness

**Files:**
- Create: `chicago-pipeline/webapp/__init__.py`
- Create: `chicago-pipeline/webapp/app.py`
- Create: `chicago-pipeline/webapp/__main__.py`
- Create: `chicago-pipeline/webapp/routes.py`
- Create: `chicago-pipeline/webapp/templates/index.html`
- Modify: `chicago-pipeline/requirements.txt`
- Modify: `chicago-pipeline/tests/conftest.py`
- Create: `chicago-pipeline/tests/test_webapp_routes.py`

- [ ] **Step 1: Add Flask to requirements and install**

Edit `chicago-pipeline/requirements.txt` — append `flask==3.0.3` on its own line.

Run: `cd chicago-pipeline && source .venv/bin/activate && pip install -r requirements.txt`
Expected: `Successfully installed flask-3.0.3 ...`

- [ ] **Step 2: Write failing test for the index route**

Create `chicago-pipeline/tests/test_webapp_routes.py`:

```python
import pytest
from webapp.app import create_app


@pytest.fixture
def client(db_path):
    app = create_app(db_path=db_path, feature_outreach=False)
    app.testing = True
    return app.test_client()


def test_index_returns_200_and_html(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.mimetype == "text/html"
    assert b"Chicago Multifamily Pipeline" in resp.data
```

- [ ] **Step 3: Run the test — verify it fails**

Run: `cd chicago-pipeline && pytest tests/test_webapp_routes.py -v`
Expected: `ModuleNotFoundError: No module named 'webapp'`

- [ ] **Step 4: Create package marker**

Create `chicago-pipeline/webapp/__init__.py`:

```python
from webapp.app import create_app

__all__ = ["create_app"]
```

- [ ] **Step 5: Create the Flask app factory**

Create `chicago-pipeline/webapp/app.py`:

```python
from __future__ import annotations
from pathlib import Path
from flask import Flask


def create_app(db_path: Path, feature_outreach: bool = False) -> Flask:
    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
    )
    app.config["DB_PATH"] = Path(db_path)
    app.config["FEATURE_OUTREACH"] = feature_outreach

    from webapp import routes
    routes.register(app)
    return app
```

- [ ] **Step 6: Create routes module with just the index handler**

Create `chicago-pipeline/webapp/routes.py`:

```python
from __future__ import annotations
from flask import Flask, current_app, render_template


def register(app: Flask) -> None:
    @app.get("/")
    def index():
        return render_template(
            "index.html",
            feature_outreach=current_app.config["FEATURE_OUTREACH"],
        )
```

- [ ] **Step 7: Create minimal index template**

Create `chicago-pipeline/webapp/templates/index.html`:

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Chicago Multifamily Pipeline — Review UI</title>
</head>
<body>
<h1>Chicago Multifamily Pipeline</h1>
</body>
</html>
```

- [ ] **Step 8: Run the test — verify it passes**

Run: `cd chicago-pipeline && pytest tests/test_webapp_routes.py -v`
Expected: `test_index_returns_200_and_html PASSED`

- [ ] **Step 9: Create CLI entrypoint**

Create `chicago-pipeline/webapp/__main__.py`:

```python
from __future__ import annotations
import argparse
from pathlib import Path
from webapp.app import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="Chicago Pipeline Review UI")
    parser.add_argument("--db", type=Path, default=Path("data/smoke.db"),
                        help="Path to SQLite database (default: data/smoke.db)")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--outreach", action="store_true",
                        help="Enable outreach UI (Plan 4 — not implemented)")
    args = parser.parse_args()

    if not args.db.exists():
        raise SystemExit(f"Database not found: {args.db}")

    app = create_app(db_path=args.db, feature_outreach=args.outreach)
    app.run(host="127.0.0.1", port=args.port, debug=True)


if __name__ == "__main__":
    main()
```

- [ ] **Step 10: Smoke-test the CLI**

Run: `cd chicago-pipeline && python -m webapp --db data/smoke.db --port 5001 &` then `curl -s http://127.0.0.1:5001/ | grep -q 'Chicago Multifamily Pipeline' && echo OK`, then `kill %1`.
Expected: `OK` printed, server stops cleanly.

- [ ] **Step 11: Commit**

```bash
cd chicago-pipeline && git add webapp/ requirements.txt tests/test_webapp_routes.py
git commit -m "feat(webapp): Flask app factory with index route"
```

---

## Task 2: Populated test DB fixture

The existing `db_path` fixture in `conftest.py` creates an empty schema. Filter and query tests need realistic rows. We copy `data/smoke.db` into `tmp_path` per test so tests are isolated.

**Files:**
- Modify: `chicago-pipeline/tests/conftest.py`

- [ ] **Step 1: Add fixture that copies smoke.db into tmp_path**

Append to `chicago-pipeline/tests/conftest.py`:

```python
import shutil

SMOKE_DB = Path(__file__).resolve().parent.parent / "data" / "smoke.db"


@pytest.fixture
def populated_db_path(tmp_path):
    """Isolated copy of data/smoke.db (641 parcels) for webapp tests."""
    if not SMOKE_DB.exists():
        pytest.skip(f"smoke.db not present at {SMOKE_DB}")
    dst = tmp_path / "smoke.db"
    shutil.copy(SMOKE_DB, dst)
    return dst
```

- [ ] **Step 2: Verify fixture is usable**

Run: `cd chicago-pipeline && pytest tests/conftest.py --collect-only -q`
Expected: no errors collecting.

- [ ] **Step 3: Commit**

```bash
cd chicago-pipeline && git add tests/conftest.py
git commit -m "test(webapp): add populated_db_path fixture"
```

---

## Task 3: Filter schema config file

Defines the filter groups that the UI exposes. Columns listed here must exist on `parcels`; the filter_schema module introspects their SQLite types and distinct values at runtime.

**Files:**
- Create: `chicago-pipeline/config/ui_filters.yaml`

- [ ] **Step 1: Write the YAML**

Create `chicago-pipeline/config/ui_filters.yaml`:

```yaml
# Filter panel config for the Review UI.
# Adding a new filter = add an entry pointing at a column in `parcels`.
# The UI introspects the column's SQLite type and distinct values at load time.
#
# Control type is inferred from the SQLite column type if omitted:
#   INTEGER/REAL       -> range (unless values are only 0/1 -> checkbox)
#   TEXT (date-like)   -> date_range   (explicit override via `type: date_range`)
#   TEXT (categorical) -> dropdown
#   TEXT (free-form)   -> text_search  (explicit override via `type: text_search`)
# Explicit `type:` always wins over inference.

filter_groups:
  - group: Score
    filters:
      - column: score
        label: Score
        type: range

  - group: Owner
    filters:
      - column: is_absentee
        label: Absentee owner
        type: checkbox
      - column: is_llc
        label: LLC ownership
        type: checkbox
      - column: owner_name
        label: Owner name
        type: text_search

  - group: Property
    filters:
      - column: property_class
        label: Property class
        type: dropdown
      - column: lot_size_sf
        label: Lot size (SF)
        type: range
      - column: year_built
        label: Year built
        type: range
      - column: condition
        label: Condition
        type: dropdown

  - group: Zoning
    filters:
      - column: zone_class
        label: Zone class
        type: dropdown
      - column: allows_multifamily_by_right
        label: Multifamily by right
        type: checkbox
      - column: far_gap
        label: FAR gap
        type: range
      - column: tif_district
        label: In TIF district
        type: dropdown

  - group: Motivation Signals
    filters:
      - column: tax_delinquent
        label: Tax delinquent
        type: checkbox
      - column: open_violations_count
        label: Open violations (min)
        type: range
      - column: has_vacancy_report
        label: Vacancy report
        type: checkbox
      - column: years_since_last_permit
        label: Years since last permit
        type: range
      - column: hold_duration_years
        label: Hold duration (years)
        type: range

  - group: Financial
    filters:
      - column: assessed_total
        label: Assessed value
        type: range
      - column: land_building_ratio
        label: Land/bldg ratio
        type: range
      - column: tax_increase_pct_5yr
        label: Tax increase 5yr (%)
        type: range

stage_pills:
  column: stage
  values: [scored, outreach, responded, introduced, dead]
```

- [ ] **Step 2: Commit**

```bash
cd chicago-pipeline && git add config/ui_filters.yaml
git commit -m "feat(webapp): add ui_filters.yaml"
```

---

## Task 4: Filter schema service

Loads `ui_filters.yaml`, introspects the DB, and emits a JSON-serializable schema the frontend uses to render the panel.

**Files:**
- Create: `chicago-pipeline/webapp/filter_schema.py`
- Create: `chicago-pipeline/tests/test_webapp_filter_schema.py`

- [ ] **Step 1: Write failing test**

Create `chicago-pipeline/tests/test_webapp_filter_schema.py`:

```python
from pathlib import Path
from webapp.filter_schema import build_filter_schema

CONFIG = Path(__file__).resolve().parent.parent / "config" / "ui_filters.yaml"


def test_schema_has_expected_groups(populated_db_path):
    schema = build_filter_schema(populated_db_path, CONFIG)
    group_names = [g["group"] for g in schema["filter_groups"]]
    assert group_names == ["Score", "Owner", "Property", "Zoning",
                           "Motivation Signals", "Financial"]


def test_range_filter_emits_min_max(populated_db_path):
    schema = build_filter_schema(populated_db_path, CONFIG)
    prop = next(g for g in schema["filter_groups"] if g["group"] == "Property")
    lot = next(f for f in prop["filters"] if f["column"] == "lot_size_sf")
    assert lot["type"] == "range"
    # smoke.db has some lot_size_sf values; min/max should be present
    assert "min" in lot and "max" in lot


def test_dropdown_filter_has_distinct_values(populated_db_path):
    schema = build_filter_schema(populated_db_path, CONFIG)
    prop = next(g for g in schema["filter_groups"] if g["group"] == "Property")
    pc = next(f for f in prop["filters"] if f["column"] == "property_class")
    assert pc["type"] == "dropdown"
    assert isinstance(pc["options"], list)
    assert len(pc["options"]) > 0


def test_checkbox_filter_has_no_extras(populated_db_path):
    schema = build_filter_schema(populated_db_path, CONFIG)
    owner = next(g for g in schema["filter_groups"] if g["group"] == "Owner")
    abs_f = next(f for f in owner["filters"] if f["column"] == "is_absentee")
    assert abs_f["type"] == "checkbox"
    assert "options" not in abs_f


def test_stage_pills_in_schema(populated_db_path):
    schema = build_filter_schema(populated_db_path, CONFIG)
    assert schema["stage_pills"]["column"] == "stage"
    assert "scored" in schema["stage_pills"]["values"]
```

- [ ] **Step 2: Run test — verify failure**

Run: `cd chicago-pipeline && pytest tests/test_webapp_filter_schema.py -v`
Expected: `ModuleNotFoundError: No module named 'webapp.filter_schema'`

- [ ] **Step 3: Implement filter_schema.py**

Create `chicago-pipeline/webapp/filter_schema.py`:

```python
from __future__ import annotations
import sqlite3
from pathlib import Path
from typing import Any
import yaml


def build_filter_schema(db_path: Path, config_path: Path) -> dict[str, Any]:
    """
    Load ui_filters.yaml and enrich each filter with live DB info:
      - range: min/max from SELECT MIN/MAX
      - dropdown: distinct non-null values from SELECT DISTINCT
      - checkbox: no enrichment
      - text_search: no enrichment
      - date_range: min/max date strings
    Output is JSON-serializable for direct return from the /api/filters route.
    """
    with config_path.open() as f:
        raw = yaml.safe_load(f) or {}

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        groups = []
        for group in raw.get("filter_groups", []):
            enriched_filters = []
            for f in group.get("filters", []):
                enriched_filters.append(_enrich_filter(conn, f))
            groups.append({"group": group["group"], "filters": enriched_filters})

        return {
            "filter_groups": groups,
            "stage_pills": raw.get("stage_pills", {}),
        }
    finally:
        conn.close()


def _enrich_filter(conn: sqlite3.Connection, f: dict) -> dict:
    col = f["column"]
    ftype = f["type"]
    out = {"column": col, "label": f["label"], "type": ftype}

    if ftype == "range":
        row = conn.execute(
            f"SELECT MIN({col}) AS mn, MAX({col}) AS mx FROM parcels "
            f"WHERE {col} IS NOT NULL"
        ).fetchone()
        out["min"] = row["mn"]
        out["max"] = row["mx"]

    elif ftype == "dropdown":
        rows = conn.execute(
            f"SELECT DISTINCT {col} AS v FROM parcels "
            f"WHERE {col} IS NOT NULL AND {col} != '' ORDER BY {col}"
        ).fetchall()
        out["options"] = [r["v"] for r in rows]

    elif ftype == "date_range":
        row = conn.execute(
            f"SELECT MIN({col}) AS mn, MAX({col}) AS mx FROM parcels "
            f"WHERE {col} IS NOT NULL AND {col} != ''"
        ).fetchone()
        out["min"] = row["mn"]
        out["max"] = row["mx"]

    # checkbox, text_search: nothing to enrich
    return out
```

- [ ] **Step 4: Run test — verify it passes**

Run: `cd chicago-pipeline && pytest tests/test_webapp_filter_schema.py -v`
Expected: all five tests PASS.

- [ ] **Step 5: Commit**

```bash
cd chicago-pipeline && git add webapp/filter_schema.py tests/test_webapp_filter_schema.py
git commit -m "feat(webapp): build_filter_schema loads yaml + introspects sqlite"
```

---

## Task 5: Parcel query builder

Pure function that converts a filter state dict (from the frontend) into a parameterized SQL `WHERE` clause + params list, plus `ORDER BY` + pagination.

**Files:**
- Create: `chicago-pipeline/webapp/parcel_query.py`
- Create: `chicago-pipeline/tests/test_webapp_parcel_query.py`

- [ ] **Step 1: Write failing tests**

Create `chicago-pipeline/tests/test_webapp_parcel_query.py`:

```python
import sqlite3
from webapp.parcel_query import build_parcel_query


def _run(db_path, sql, params):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def test_empty_filters_returns_all_with_default_sort(populated_db_path):
    sql, params = build_parcel_query(filters={}, stage=None, limit=20, offset=0)
    rows = _run(populated_db_path, sql, params)
    assert len(rows) == 20  # pagination cap
    # Default sort is last_updated_date DESC, then hold_duration_years DESC
    assert "ORDER BY" in sql
    assert "last_updated_date DESC" in sql


def test_checkbox_filter_is_absentee(populated_db_path):
    sql, params = build_parcel_query(
        filters={"is_absentee": True}, stage=None, limit=1000, offset=0
    )
    rows = _run(populated_db_path, sql, params)
    assert len(rows) == 568  # known count from smoke.db
    assert all(r["is_absentee"] == 1 for r in rows)


def test_range_filter_hold_duration_min_only(populated_db_path):
    sql, params = build_parcel_query(
        filters={"hold_duration_years": {"min": 20}},
        stage=None, limit=1000, offset=0,
    )
    rows = _run(populated_db_path, sql, params)
    assert all(r["hold_duration_years"] >= 20 for r in rows)
    assert len(rows) > 0


def test_range_filter_hold_duration_min_and_max(populated_db_path):
    sql, params = build_parcel_query(
        filters={"hold_duration_years": {"min": 5, "max": 10}},
        stage=None, limit=1000, offset=0,
    )
    rows = _run(populated_db_path, sql, params)
    assert all(5 <= r["hold_duration_years"] <= 10 for r in rows)


def test_dropdown_filter_property_class(populated_db_path):
    sql, params = build_parcel_query(
        filters={"property_class": "299"},
        stage=None, limit=1000, offset=0,
    )
    rows = _run(populated_db_path, sql, params)
    assert all(r["property_class"] == "299" for r in rows)
    assert len(rows) > 0


def test_stage_filter(populated_db_path):
    sql, params = build_parcel_query(filters={}, stage="scored", limit=1000, offset=0)
    rows = _run(populated_db_path, sql, params)
    assert all(r["stage"] == "scored" for r in rows)


def test_text_search_owner_name(populated_db_path):
    sql, params = build_parcel_query(
        filters={"owner_name": "LLC"},
        stage=None, limit=1000, offset=0,
    )
    rows = _run(populated_db_path, sql, params)
    # All matching rows contain "LLC" (case-insensitive)
    assert all("LLC" in (r["owner_name"] or "").upper() for r in rows)


def test_column_name_rejects_injection():
    import pytest
    with pytest.raises(ValueError, match="unknown column"):
        build_parcel_query(
            filters={"owner_name; DROP TABLE parcels; --": True},
            stage=None, limit=20, offset=0,
        )


def test_count_query_matches_result_query(populated_db_path):
    from webapp.parcel_query import build_count_query
    filters = {"is_absentee": True}
    list_sql, list_params = build_parcel_query(
        filters=filters, stage=None, limit=10000, offset=0
    )
    count_sql, count_params = build_count_query(filters=filters, stage=None)
    n_rows = len(_run(populated_db_path, list_sql, list_params))
    n_count = _run(populated_db_path, count_sql, count_params)[0]["n"]
    assert n_rows == n_count == 568
```

- [ ] **Step 2: Run tests — verify failure**

Run: `cd chicago-pipeline && pytest tests/test_webapp_parcel_query.py -v`
Expected: `ModuleNotFoundError: No module named 'webapp.parcel_query'`

- [ ] **Step 3: Implement parcel_query.py**

Create `chicago-pipeline/webapp/parcel_query.py`:

```python
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
        "SELECT pin, address, lat, lng, property_class, lot_size_sf, "
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
```

- [ ] **Step 4: Run tests — verify all pass**

Run: `cd chicago-pipeline && pytest tests/test_webapp_parcel_query.py -v`
Expected: all nine tests PASS.

- [ ] **Step 5: Commit**

```bash
cd chicago-pipeline && git add webapp/parcel_query.py tests/test_webapp_parcel_query.py
git commit -m "feat(webapp): build_parcel_query with injection-safe column whitelist"
```

---

## Task 6: JSON API routes

Add `/api/filters`, `/api/parcels`, `/api/parcels/<pin>`, `/api/map-data` to `routes.py`.

**Files:**
- Modify: `chicago-pipeline/webapp/routes.py`
- Modify: `chicago-pipeline/tests/test_webapp_routes.py`

- [ ] **Step 1: Write failing tests for all four API routes**

Append to `chicago-pipeline/tests/test_webapp_routes.py`:

```python
import json


@pytest.fixture
def pop_client(populated_db_path):
    app = create_app(db_path=populated_db_path, feature_outreach=False)
    app.testing = True
    return app.test_client()


def test_api_filters_returns_schema(pop_client):
    resp = pop_client.get("/api/filters")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "filter_groups" in data
    assert any(g["group"] == "Owner" for g in data["filter_groups"])


def test_api_parcels_default_pagination(pop_client):
    resp = pop_client.get("/api/parcels")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["total"] == 641
    assert len(data["parcels"]) == 20
    assert "pin" in data["parcels"][0]
    assert "address" in data["parcels"][0]


def test_api_parcels_applies_filter(pop_client):
    resp = pop_client.get("/api/parcels?is_absentee=true&limit=1000")
    data = resp.get_json()
    assert data["total"] == 568
    assert all(p["is_absentee"] == 1 for p in data["parcels"])


def test_api_parcels_range_filter(pop_client):
    resp = pop_client.get(
        "/api/parcels?hold_duration_years.min=20&limit=1000"
    )
    data = resp.get_json()
    assert data["total"] > 0
    assert all(p["hold_duration_years"] >= 20 for p in data["parcels"])


def test_api_parcel_detail(pop_client):
    # Known PIN from smoke.db
    resp = pop_client.get("/api/parcels/14291270060000")
    assert resp.status_code == 200
    p = resp.get_json()
    assert p["pin"] == "14291270060000"
    assert p["address"] == "2847 N LINCOLN AVE"
    # Google Maps URL is derived server-side
    assert "google_maps_url" in p


def test_api_parcel_detail_404(pop_client):
    resp = pop_client.get("/api/parcels/00000000000000")
    assert resp.status_code == 404


def test_api_map_data_is_geojson(pop_client):
    resp = pop_client.get("/api/map-data")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["type"] == "FeatureCollection"
    assert len(data["features"]) > 0
    feat = data["features"][0]
    assert feat["type"] == "Feature"
    assert feat["geometry"]["type"] == "Point"
    assert "pin" in feat["properties"]
    assert "category" in feat["properties"]
    # Valid categories: top, consolidated, outreach, listed, other
    assert feat["properties"]["category"] in {
        "top", "consolidated", "outreach", "listed", "other"
    }


def test_api_map_data_marks_consolidated(pop_client):
    resp = pop_client.get("/api/map-data")
    data = resp.get_json()
    cats = [f["properties"]["category"] for f in data["features"]]
    # smoke.db has 68 parcels with consolidation_group_id
    assert cats.count("consolidated") == 68
```

- [ ] **Step 2: Run tests — verify failure**

Run: `cd chicago-pipeline && pytest tests/test_webapp_routes.py -v`
Expected: new tests FAIL with 404 or AttributeError.

- [ ] **Step 3: Extend routes.py with API handlers**

Replace `chicago-pipeline/webapp/routes.py` contents with:

```python
from __future__ import annotations
import sqlite3
from pathlib import Path
from typing import Any
from flask import Flask, abort, current_app, jsonify, render_template, request

from webapp.filter_schema import build_filter_schema
from webapp.parcel_query import (
    ALLOWED_FILTER_COLUMNS,
    build_count_query,
    build_parcel_query,
)


UI_FILTERS_YAML = Path(__file__).resolve().parent.parent / "config" / "ui_filters.yaml"


def register(app: Flask) -> None:
    @app.get("/")
    def index():
        return render_template(
            "index.html",
            feature_outreach=current_app.config["FEATURE_OUTREACH"],
        )

    @app.get("/api/filters")
    def api_filters():
        schema = build_filter_schema(
            current_app.config["DB_PATH"], UI_FILTERS_YAML
        )
        return jsonify(schema)

    @app.get("/api/parcels")
    def api_parcels():
        filters = _parse_filters(request.args)
        stage = request.args.get("stage") or None
        limit = min(int(request.args.get("limit", 20)), 1000)
        offset = int(request.args.get("offset", 0))

        list_sql, list_params = build_parcel_query(filters, stage, limit, offset)
        count_sql, count_params = build_count_query(filters, stage)

        with _conn() as conn:
            parcels = [dict(r) for r in conn.execute(list_sql, list_params)]
            total = conn.execute(count_sql, count_params).fetchone()["n"]

        return jsonify({"total": total, "parcels": parcels})

    @app.get("/api/parcels/<pin>")
    def api_parcel_detail(pin: str):
        with _conn() as conn:
            row = conn.execute(
                "SELECT * FROM parcels WHERE pin = ?", (pin,)
            ).fetchone()
            if row is None:
                abort(404)
            parcel = dict(row)

            # Attach any contact rows (will be empty in smoke.db)
            contacts = [
                dict(r) for r in conn.execute(
                    "SELECT * FROM contacts WHERE pin = ?", (pin,)
                )
            ]
            parcel["contacts"] = contacts

        parcel["google_maps_url"] = _google_maps_url(parcel)
        return jsonify(parcel)

    @app.get("/api/map-data")
    def api_map_data():
        filters = _parse_filters(request.args)
        stage = request.args.get("stage") or None
        # Map gets up to 5000 pins (all of smoke.db)
        sql, params = build_parcel_query(filters, stage, limit=5000, offset=0)

        with _conn() as conn:
            rows = [dict(r) for r in conn.execute(sql, params)]

        features = []
        for r in rows:
            if r["lat"] is None or r["lng"] is None:
                continue
            features.append({
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [r["lng"], r["lat"]],
                },
                "properties": {
                    "pin": r["pin"],
                    "address": r["address"],
                    "score": r["score"],
                    "category": _map_category(r),
                },
            })

        return jsonify({"type": "FeatureCollection", "features": features})


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(current_app.config["DB_PATH"])
    conn.row_factory = sqlite3.Row
    return conn


def _parse_filters(args) -> dict[str, Any]:
    """Parse query string into the dict shape parcel_query expects.

    Conventions:
      ?is_absentee=true        -> {"is_absentee": True}
      ?property_class=211      -> {"property_class": "211"}
      ?hold_duration_years.min=20  -> {"hold_duration_years": {"min": 20.0}}
      ?hold_duration_years.max=30
    """
    filters: dict[str, Any] = {}
    for key, value in args.items():
        if key in {"limit", "offset", "stage", "sort"}:
            continue

        if "." in key:
            col, suffix = key.split(".", 1)
            if col not in ALLOWED_FILTER_COLUMNS or suffix not in {"min", "max"}:
                continue
            try:
                num = float(value)
            except ValueError:
                continue
            filters.setdefault(col, {})[suffix] = num
            continue

        if key not in ALLOWED_FILTER_COLUMNS:
            continue

        if value.lower() in {"true", "1"}:
            filters[key] = True
        elif value.lower() in {"false", "0"}:
            # Omit — we don't filter "must be false" for checkboxes
            continue
        else:
            filters[key] = value

    return filters


def _map_category(row: dict) -> str:
    """Pin color bucket. Scoring not implemented, so 'top' is never emitted yet."""
    if row.get("listing_status") == "listed":
        return "listed"
    if row.get("stage") == "outreach":
        return "outreach"
    if row.get("consolidation_group_id") is not None:
        return "consolidated"
    return "other"


def _google_maps_url(parcel: dict) -> str:
    if parcel.get("lat") is not None and parcel.get("lng") is not None:
        return f"https://www.google.com/maps?q={parcel['lat']},{parcel['lng']}"
    if parcel.get("address"):
        from urllib.parse import quote_plus
        return f"https://www.google.com/maps?q={quote_plus(parcel['address'] + ', Chicago, IL')}"
    return "https://www.google.com/maps"
```

- [ ] **Step 4: Run tests — verify they pass**

Run: `cd chicago-pipeline && pytest tests/test_webapp_routes.py -v`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
cd chicago-pipeline && git add webapp/routes.py tests/test_webapp_routes.py
git commit -m "feat(webapp): JSON APIs for filters, parcels, detail, map"
```

---

## Task 7: Static CSS — extracted and adapted from mockup

Lift the `<style>` block from `mockup/index.html` into a standalone stylesheet. No behavior change — the UI styles stay identical.

**Files:**
- Create: `chicago-pipeline/webapp/static/css/style.css`

- [ ] **Step 1: Copy mockup styles**

Create `chicago-pipeline/webapp/static/css/style.css` containing the complete CSS block from `mockup/index.html` lines 9–151. Copy verbatim. No changes.

- [ ] **Step 2: Verify file size**

Run: `cd chicago-pipeline && wc -l webapp/static/css/style.css`
Expected: approximately 142 lines (matching the mockup CSS block).

- [ ] **Step 3: Commit**

```bash
cd chicago-pipeline && git add webapp/static/css/style.css
git commit -m "feat(webapp): static CSS extracted from mockup"
```

---

## Task 8: Index template — three-column shell

Translate the mockup's HTML structure into a Jinja template. Mock data is replaced with empty containers that JS will populate. Outreach-flagged sections are wrapped in `{% if feature_outreach %}`.

**Files:**
- Modify: `chicago-pipeline/webapp/templates/index.html`
- Create: `chicago-pipeline/webapp/static/js/app.js` (empty placeholder)

- [ ] **Step 1: Replace index.html with the full shell**

Replace `chicago-pipeline/webapp/templates/index.html` contents with:

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Chicago Multifamily Pipeline — Review UI</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
<link rel="stylesheet" href="{{ url_for('static', filename='css/style.css') }}" />
</head>
<body>

<div class="top-bar">
  <h1>Chicago Multifamily Pipeline</h1>
  <div class="meta" id="top-bar-meta">Score v N/A · loading… · Top 20 shown</div>
</div>

{% if feature_outreach %}
<div class="due-today-banner" id="due-today-banner">
  <div class="label">DUE TODAY</div>
  <div id="due-badges"></div>
</div>
{% endif %}

<div class="main"{% if not feature_outreach %} style="height: calc(100vh - 56px);"{% endif %}>

  <!-- LEFT: Ranked List -->
  <div class="left-panel">
    <div class="panel-header">
      <h2>Ranked Parcels</h2>
      <span class="count-label" id="count-label">— of —</span>
    </div>
    <div class="filter-row" id="stage-pills"></div>
    <div class="filter-toggle-row">
      <button class="filter-toggle-btn" id="filter-toggle">
        <span class="arrow">▸</span> Filters <span class="active-filter-count" id="active-filter-count">0</span>
      </button>
      <button class="filter-toggle-btn" id="clear-filters" style="color:#8b949e; font-size:10px;">Clear all</button>
    </div>
    <div class="filter-panel" id="filter-panel"></div>
    <div class="parcel-list" id="parcel-list"></div>
    <div style="padding: 8px 16px; text-align: center; border-bottom: 1px solid #30363d;">
      <button class="btn btn-sm btn-outline" id="load-more" style="width:100%;">Load more</button>
    </div>
    {% if feature_outreach %}
    <div class="batch-actions">
      <button class="btn btn-primary">Draft Outreach for Selected</button>
    </div>
    {% endif %}
  </div>

  <!-- CENTER: Map -->
  <div class="center-panel">
    <div id="map"></div>
    <div class="layer-toggles">
      <h3>Layers</h3>
      <label class="layer-toggle"><input type="checkbox" checked data-layer="top"> <span class="layer-dot" style="background:#238636"></span> Top-N scored</label>
      <label class="layer-toggle"><input type="checkbox" checked data-layer="consolidated"> <span class="layer-dot" style="background:#a855f7"></span> Consolidated</label>
      <label class="layer-toggle"><input type="checkbox" checked data-layer="outreach"> <span class="layer-dot" style="background:#58a6ff"></span> Outreach sent</label>
      <label class="layer-toggle"><input type="checkbox" checked data-layer="listed"> <span class="layer-dot" style="background:#f0883e"></span> Listed</label>
      <label class="layer-toggle"><input type="checkbox" checked data-layer="other"> <span class="layer-dot" style="background:#484f58"></span> All others</label>
    </div>
  </div>

  <!-- RIGHT: Detail Panel -->
  <div class="right-panel" id="detail-panel">
    <div class="detail-section" style="color:#8b949e; font-size:12px;">
      Select a parcel to see details.
    </div>
  </div>
</div>

<script>
  window.FEATURE_OUTREACH = {{ feature_outreach|tojson }};
</script>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script src="{{ url_for('static', filename='js/filters.js') }}"></script>
<script src="{{ url_for('static', filename='js/list.js') }}"></script>
<script src="{{ url_for('static', filename='js/map.js') }}"></script>
<script src="{{ url_for('static', filename='js/detail.js') }}"></script>
<script src="{{ url_for('static', filename='js/app.js') }}"></script>
</body>
</html>
```

- [ ] **Step 2: Create empty JS placeholders (so url_for resolves)**

Run: `cd chicago-pipeline && mkdir -p webapp/static/js`

Then create four empty files so the `<script src>` tags don't 404 before the later tasks fill them in:

```bash
touch webapp/static/js/filters.js webapp/static/js/list.js webapp/static/js/map.js webapp/static/js/detail.js webapp/static/js/app.js
```

- [ ] **Step 3: Smoke-test the page loads**

Run:
```bash
cd chicago-pipeline && python -m webapp --db data/smoke.db --port 5001 &
sleep 1
curl -s http://127.0.0.1:5001/ | grep -c 'parcel-list'
kill %1
```
Expected: `1` (the parcel-list div is present).

- [ ] **Step 4: Commit**

```bash
cd chicago-pipeline && git add webapp/templates/index.html webapp/static/js/
git commit -m "feat(webapp): three-column index shell"
```

---

## Task 9: Filter panel JS

Fetches `/api/filters`, renders each control, collects state, dispatches `filterchange` events on the window.

**Files:**
- Modify: `chicago-pipeline/webapp/static/js/filters.js`

- [ ] **Step 1: Implement filters.js**

Replace `chicago-pipeline/webapp/static/js/filters.js` with:

```javascript
// Filter panel: renders controls from /api/filters, collects state into
// window.FilterState, dispatches 'filterchange' when anything changes.

window.FilterState = {
  filters: {},   // { column: true | "value" | {min, max} }
  stage: null,
};

(async function initFilters() {
  const schema = await fetch('/api/filters').then(r => r.json());

  renderStagePills(schema.stage_pills);
  renderFilterPanel(schema.filter_groups);
  wireFilterToggle();

  window.dispatchEvent(new CustomEvent('filterchange'));
})();

function renderStagePills(cfg) {
  const container = document.getElementById('stage-pills');
  const pills = [{label: 'All', value: null}]
    .concat((cfg.values || []).map(v => ({label: capitalize(v), value: v})));

  pills.forEach((p, i) => {
    const el = document.createElement('div');
    el.className = 'filter-pill' + (i === 0 ? ' active' : '');
    el.textContent = p.label;
    el.onclick = () => {
      container.querySelectorAll('.filter-pill').forEach(e => e.classList.remove('active'));
      el.classList.add('active');
      window.FilterState.stage = p.value;
      window.dispatchEvent(new CustomEvent('filterchange'));
    };
    container.appendChild(el);
  });
}

function renderFilterPanel(groups) {
  const panel = document.getElementById('filter-panel');
  groups.forEach(g => {
    const groupEl = document.createElement('div');
    groupEl.className = 'filter-group';
    groupEl.innerHTML = `<div class="filter-group-title">${g.group}</div>`;
    g.filters.forEach(f => groupEl.appendChild(renderFilter(f)));
    panel.appendChild(groupEl);
  });
}

function renderFilter(f) {
  const ctrl = document.createElement('div');
  ctrl.className = 'filter-control';

  if (f.type === 'checkbox') {
    ctrl.innerHTML = `
      <input type="checkbox" class="filter-checkbox" data-col="${f.column}">
      <label>${f.label}</label>
    `;
    ctrl.querySelector('input').onchange = (e) => {
      if (e.target.checked) window.FilterState.filters[f.column] = true;
      else delete window.FilterState.filters[f.column];
      updateActiveCount();
      window.dispatchEvent(new CustomEvent('filterchange'));
    };
  } else if (f.type === 'range') {
    ctrl.style.flexDirection = 'column';
    ctrl.style.alignItems = 'flex-start';
    ctrl.style.gap = '4px';
    ctrl.innerHTML = `
      <label style="font-size:10px; color:#8b949e;">${f.label}</label>
      <div class="filter-range">
        <input type="number" class="filter-input" placeholder="Min" data-col="${f.column}" data-bound="min">
        <span style="color:#484f58;">—</span>
        <input type="number" class="filter-input" placeholder="Max" data-col="${f.column}" data-bound="max">
      </div>
    `;
    ctrl.querySelectorAll('input').forEach(i => {
      i.onchange = (e) => {
        const col = e.target.dataset.col;
        const bound = e.target.dataset.bound;
        const val = e.target.value === '' ? null : parseFloat(e.target.value);
        const cur = window.FilterState.filters[col] || {};
        if (val === null) delete cur[bound];
        else cur[bound] = val;
        if (Object.keys(cur).length === 0) delete window.FilterState.filters[col];
        else window.FilterState.filters[col] = cur;
        updateActiveCount();
        window.dispatchEvent(new CustomEvent('filterchange'));
      };
    });
  } else if (f.type === 'dropdown') {
    ctrl.style.flexDirection = 'column';
    ctrl.style.alignItems = 'flex-start';
    ctrl.style.gap = '4px';
    const opts = (f.options || []).map(o => `<option value="${o}">${o}</option>`).join('');
    ctrl.innerHTML = `
      <label style="font-size:10px; color:#8b949e;">${f.label}</label>
      <select class="filter-select" data-col="${f.column}">
        <option value="">Any</option>
        ${opts}
      </select>
    `;
    ctrl.querySelector('select').onchange = (e) => {
      const col = e.target.dataset.col;
      if (e.target.value === '') delete window.FilterState.filters[col];
      else window.FilterState.filters[col] = e.target.value;
      updateActiveCount();
      window.dispatchEvent(new CustomEvent('filterchange'));
    };
  } else if (f.type === 'text_search') {
    ctrl.style.flexDirection = 'column';
    ctrl.style.alignItems = 'flex-start';
    ctrl.style.gap = '4px';
    ctrl.innerHTML = `
      <label style="font-size:10px; color:#8b949e;">${f.label}</label>
      <input type="text" class="filter-input" style="width:100%;" placeholder="Search…" data-col="${f.column}">
    `;
    ctrl.querySelector('input').onchange = (e) => {
      const col = e.target.dataset.col;
      if (e.target.value === '') delete window.FilterState.filters[col];
      else window.FilterState.filters[col] = e.target.value;
      updateActiveCount();
      window.dispatchEvent(new CustomEvent('filterchange'));
    };
  }

  return ctrl;
}

function updateActiveCount() {
  const n = Object.keys(window.FilterState.filters).length;
  document.getElementById('active-filter-count').textContent = n;
}

function wireFilterToggle() {
  const btn = document.getElementById('filter-toggle');
  const panel = document.getElementById('filter-panel');
  btn.onclick = () => {
    panel.classList.toggle('open');
    btn.querySelector('.arrow').textContent =
      panel.classList.contains('open') ? '▾' : '▸';
  };
  document.getElementById('clear-filters').onclick = () => {
    window.FilterState.filters = {};
    // Reset all inputs
    panel.querySelectorAll('input[type="checkbox"]').forEach(i => i.checked = false);
    panel.querySelectorAll('input[type="number"], input[type="text"]').forEach(i => i.value = '');
    panel.querySelectorAll('select').forEach(s => s.value = '');
    updateActiveCount();
    window.dispatchEvent(new CustomEvent('filterchange'));
  };
}

function capitalize(s) { return s[0].toUpperCase() + s.slice(1); }

// Helper used by list.js and map.js to build query strings
window.filterStateToQuery = function() {
  const params = new URLSearchParams();
  for (const [col, val] of Object.entries(window.FilterState.filters)) {
    if (val === true) params.set(col, 'true');
    else if (typeof val === 'object') {
      if (val.min != null) params.set(`${col}.min`, val.min);
      if (val.max != null) params.set(`${col}.max`, val.max);
    } else {
      params.set(col, val);
    }
  }
  if (window.FilterState.stage) params.set('stage', window.FilterState.stage);
  return params.toString();
};
```

- [ ] **Step 2: Smoke-test in a browser (manual)**

Run: `cd chicago-pipeline && python -m webapp --db data/smoke.db`, visit `http://127.0.0.1:5000/`. Confirm: filter toggle opens the panel, filter groups render, checkbox on "Absentee owner" increments the active-filter-count badge.

- [ ] **Step 3: Commit**

```bash
cd chicago-pipeline && git add webapp/static/js/filters.js
git commit -m "feat(webapp): filter panel rendering + state"
```

---

## Task 10: Ranked list JS

Fetches `/api/parcels` on every `filterchange` event, renders parcel items, updates count label, wires clicks.

**Files:**
- Modify: `chicago-pipeline/webapp/static/js/list.js`

- [ ] **Step 1: Implement list.js**

Replace `chicago-pipeline/webapp/static/js/list.js` with:

```javascript
// Ranked list: re-fetches on filterchange, renders rows, handles selection.

const LIST_PAGE_SIZE = 20;
let currentOffset = 0;
let currentTotal = 0;

window.addEventListener('filterchange', () => {
  currentOffset = 0;
  loadList({replace: true});
});

document.getElementById('load-more').onclick = () => {
  currentOffset += LIST_PAGE_SIZE;
  loadList({replace: false});
};

async function loadList({replace}) {
  const qs = window.filterStateToQuery();
  const url = `/api/parcels?${qs}&limit=${LIST_PAGE_SIZE}&offset=${currentOffset}`;
  const data = await fetch(url).then(r => r.json());
  currentTotal = data.total;

  const list = document.getElementById('parcel-list');
  if (replace) list.innerHTML = '';

  data.parcels.forEach(p => list.appendChild(renderParcelRow(p)));

  document.getElementById('count-label').textContent =
    `${Math.min(currentOffset + LIST_PAGE_SIZE, currentTotal)} of ${currentTotal.toLocaleString()}`;
  document.getElementById('top-bar-meta').textContent =
    `Score v N/A · ${currentTotal.toLocaleString()} parcels · Top ${LIST_PAGE_SIZE} shown`;
  document.getElementById('load-more').style.display =
    (currentOffset + LIST_PAGE_SIZE) < currentTotal ? '' : 'none';
}

function renderParcelRow(p) {
  const el = document.createElement('div');
  el.className = 'parcel-item';
  el.dataset.pin = p.pin;

  const details = [
    p.lot_size_sf ? `${Math.round(p.lot_size_sf).toLocaleString()} SF lot` : null,
    p.zone_class,
    p.year_built ? `Built ${p.year_built}` : null,
    p.hold_duration_years ? `Held ${Math.round(p.hold_duration_years)}yr` : null,
  ].filter(Boolean).join(' · ') || '—';

  const tags = [];
  // Score tag — stub since score is NULL
  if (p.score != null) {
    const cls = p.score >= 80 ? 'score' : 'score-med';
    tags.push(`<span class="tag ${cls}">${Math.round(p.score)}</span>`);
  }
  if (p.is_absentee) tags.push('<span class="tag absentee">Absentee</span>');
  if (p.is_llc) tags.push('<span class="tag llc">LLC</span>');
  if (p.tax_delinquent) tags.push('<span class="tag delinquent">Tax delinquent</span>');
  if (p.far_gap && p.far_gap >= 1.5) {
    tags.push(`<span class="tag underbuilt">FAR gap ${p.far_gap.toFixed(1)}x</span>`);
  }
  if (p.consolidation_group_id != null) {
    tags.push('<span class="tag llc">Consolidated</span>');
  }
  if (p.listing_status === 'listed') {
    tags.push('<span class="tag listed">Listed</span>');
  }
  if (p.stage && p.stage !== 'scored') {
    tags.push(`<span class="tag stage">${capitalize(p.stage)}</span>`);
  }

  el.innerHTML = `
    <div class="address">${escapeHtml(p.address || p.pin)}</div>
    <div class="details">${escapeHtml(details)}</div>
    <div class="tags">${tags.join('')}</div>
  `;

  el.onclick = () => {
    document.querySelectorAll('.parcel-item.selected').forEach(e => e.classList.remove('selected'));
    el.classList.add('selected');
    window.dispatchEvent(new CustomEvent('parcelselect', {detail: {pin: p.pin}}));
  };

  return el;
}

function escapeHtml(s) {
  return (s || '').replace(/[&<>"']/g, c => ({
    '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
  }[c]));
}

function capitalize(s) { return s[0].toUpperCase() + s.slice(1); }
```

- [ ] **Step 2: Manual smoke test**

Reload the app. Confirm: ranked list populates with 20 parcels sorted by last_updated_date. Toggling "Absentee owner" filter updates the list to 568 matches with pagination working.

- [ ] **Step 3: Commit**

```bash
cd chicago-pipeline && git add webapp/static/js/list.js
git commit -m "feat(webapp): ranked list rendering"
```

---

## Task 11: Map JS

Fetches `/api/map-data`, renders CARTO dark tiles + colored circle markers, syncs with list selection and filter state.

**Files:**
- Modify: `chicago-pipeline/webapp/static/js/map.js`

- [ ] **Step 1: Implement map.js**

Replace `chicago-pipeline/webapp/static/js/map.js` with:

```javascript
// Leaflet map with category-colored pins. Re-renders on filterchange.

const CATEGORY_COLORS = {
  top: '#238636',
  consolidated: '#a855f7',
  outreach: '#58a6ff',
  listed: '#f0883e',
  other: '#484f58',
};

let map = null;
let markerLayer = null;
let markersByPin = {};
let selectionRing = null;
let layerEnabled = {
  top: true, consolidated: true, outreach: true, listed: true, other: true,
};

initMap();
window.addEventListener('filterchange', loadMap);
window.addEventListener('parcelselect', (e) => highlightSelection(e.detail.pin));

function initMap() {
  map = L.map('map', {zoomControl: false}).setView([41.9395, -87.6535], 14);
  L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
    attribution: '&copy; OpenStreetMap contributors &copy; CARTO',
    maxZoom: 19,
  }).addTo(map);
  L.control.zoom({position: 'bottomright'}).addTo(map);
  markerLayer = L.layerGroup().addTo(map);

  document.querySelectorAll('.layer-toggle input').forEach(cb => {
    cb.onchange = (e) => {
      layerEnabled[e.target.dataset.layer] = e.target.checked;
      applyLayerVisibility();
    };
  });
}

async function loadMap() {
  const qs = window.filterStateToQuery();
  const geo = await fetch(`/api/map-data?${qs}`).then(r => r.json());
  markerLayer.clearLayers();
  markersByPin = {};

  geo.features.forEach(f => {
    const [lng, lat] = f.geometry.coordinates;
    const cat = f.properties.category;
    const color = CATEGORY_COLORS[cat];
    const marker = L.circleMarker([lat, lng], {
      radius: cat === 'other' ? 3 : 7,
      color,
      fillColor: color,
      fillOpacity: cat === 'other' ? 0.5 : 0.8,
      weight: cat === 'other' ? 0 : 2,
    });
    marker.feature = f;
    marker.bindTooltip(
      `${f.properties.address || f.properties.pin}` +
      (f.properties.score != null ? ` (${Math.round(f.properties.score)})` : ''),
      {direction: 'top', offset: [0, -10]}
    );
    marker.on('click', () => {
      window.dispatchEvent(new CustomEvent('parcelselect', {detail: {pin: f.properties.pin}}));
    });
    markersByPin[f.properties.pin] = marker;
    markerLayer.addLayer(marker);
  });

  applyLayerVisibility();
}

function applyLayerVisibility() {
  markerLayer.eachLayer(m => {
    const cat = m.feature.properties.category;
    const visible = layerEnabled[cat];
    m.setStyle({opacity: visible ? 1 : 0, fillOpacity: visible ? (cat === 'other' ? 0.5 : 0.8) : 0});
  });
}

function highlightSelection(pin) {
  if (selectionRing) { map.removeLayer(selectionRing); selectionRing = null; }
  const marker = markersByPin[pin];
  if (!marker) return;
  const ll = marker.getLatLng();
  selectionRing = L.circleMarker(ll, {
    radius: 14, color: '#f0f6fc', fillColor: 'transparent',
    fillOpacity: 0, weight: 2, dashArray: '4 4',
  }).addTo(map);
  map.panTo(ll);
}
```

- [ ] **Step 2: Manual smoke test**

Reload. Confirm: map renders with CARTO dark tiles, ~641 pins appear (68 purple consolidated + gray others), clicking a pin selects it and triggers selection ring, toggling a layer checkbox hides/shows those pins.

- [ ] **Step 3: Commit**

```bash
cd chicago-pipeline && git add webapp/static/js/map.js
git commit -m "feat(webapp): Leaflet map with category pins and layer toggles"
```

---

## Task 12: Detail panel JS

Renders the right panel sections from `/api/parcels/<pin>`. Score Breakdown is stubbed. Outreach sections respect `window.FEATURE_OUTREACH`.

**Files:**
- Modify: `chicago-pipeline/webapp/static/js/detail.js`

- [ ] **Step 1: Implement detail.js**

Replace `chicago-pipeline/webapp/static/js/detail.js` with:

```javascript
// Right panel: renders parcel detail sections.

window.addEventListener('parcelselect', async (e) => {
  const p = await fetch(`/api/parcels/${e.detail.pin}`).then(r => r.json());
  renderDetail(p);
});

function renderDetail(p) {
  const panel = document.getElementById('detail-panel');
  panel.innerHTML = '';
  panel.appendChild(sectionPropertyFacts(p));
  panel.appendChild(sectionOwner(p));
  panel.appendChild(sectionZoning(p));
  panel.appendChild(sectionScoreBreakdown(p));
  panel.appendChild(sectionFinancials(p));
  // Outreach sections are only shown when feature flag is on;
  // they remain stubbed in this plan.
  if (window.FEATURE_OUTREACH) {
    panel.appendChild(sectionOutreachStub());
  }
}

function sectionPropertyFacts(p) {
  return renderSection('Property Facts', [
    ['PIN', p.pin],
    ['Address', p.address],
    ['Lot Size', p.lot_size_sf ? `${Math.round(p.lot_size_sf).toLocaleString()} SF` : null],
    ['Building SF', p.building_sf ? `${Math.round(p.building_sf).toLocaleString()} SF` : null],
    ['Year Built', p.year_built],
    ['Ward', p.ward_num],
    ['Class', p.property_class],
    ['Building Type', p.building_classification],
    ['Condition', p.condition],
    ['Open Violations', p.open_violations_count],
  ], `<div style="margin-top:10px;"><a href="${p.google_maps_url}" target="_blank" rel="noopener">Open in Google Maps →</a></div>`);
}

function sectionOwner(p) {
  const contact = (p.contacts && p.contacts[0]) || {};
  return renderSection('Owner', [
    ['Owner', p.owner_name],
    ['Type', ownerTypeLabel(p)],
    ['Mailing Address', p.mail_address],
    ['Hold Duration', p.hold_duration_years ? `${Math.round(p.hold_duration_years)} years` : null],
    ['Registered Agent', contact.role === 'registered_agent' ? contact.name : null],
    ['Phone', contact.phone],
    ['Email', contact.email],
    ['Listing Status', p.listing_status || 'Not listed'],
  ]);
}

function sectionZoning(p) {
  return renderSection('Zoning Context', [
    ['Zone Class', p.zone_class],
    ['Allows Multifamily', p.allows_multifamily_by_right === 1 ? 'By right' :
       (p.allows_multifamily_by_right === 0 ? 'Requires rezoning' : null)],
    ['Max FAR', p.max_far],
    ['Built FAR', p.built_far],
    ['FAR Gap', p.far_gap ? `${p.far_gap.toFixed(1)}x underbuilt` : null],
    ['TIF District', p.tif_district],
    ['Nearest CTA', p.cta_nearest_station],
    ['CTA Distance', p.cta_distance_ft ? `${Math.round(p.cta_distance_ft)} ft` : null],
  ]);
}

function sectionScoreBreakdown(p) {
  // STUB — scoring not yet implemented (see plan Risks section)
  const el = document.createElement('div');
  el.className = 'detail-section';
  el.innerHTML = `
    <h3>Score Breakdown</h3>
    <div style="font-size:12px; color:#8b949e; padding:8px 0;">
      Scoring not yet available — run <code style="color:#c9d1d9;">pipeline.score</code> to populate.
    </div>
  `;
  return el;
}

function sectionFinancials(p) {
  return renderSection('Financials', [
    ['Assessed Total', p.assessed_total ? `$${Math.round(p.assessed_total).toLocaleString()}` : null],
    ['Est. Annual Tax', p.estimated_annual_tax ? `$${Math.round(p.estimated_annual_tax).toLocaleString()}` : null],
    ['Tax Change (1yr)', p.tax_increase_pct_1yr != null ? `${p.tax_increase_pct_1yr.toFixed(1)}%` : null],
    ['Tax Change (5yr)', p.tax_increase_pct_5yr != null ? `${p.tax_increase_pct_5yr.toFixed(1)}%` : null],
    ['Last Sale Price', p.last_sale_price ? `$${Math.round(p.last_sale_price).toLocaleString()}` : null],
    ['Last Sale Date', p.last_sale_date],
  ]);
}

function sectionOutreachStub() {
  const el = document.createElement('div');
  el.className = 'detail-section';
  el.innerHTML = `
    <h3>Outreach</h3>
    <div style="font-size:12px; color:#8b949e;">Outreach UI is planned for a later implementation phase.</div>
  `;
  return el;
}

function ownerTypeLabel(p) {
  const parts = [];
  if (p.is_llc) parts.push('LLC');
  if (p.is_absentee) parts.push('Absentee');
  return parts.length ? parts.join(' · ') : null;
}

function renderSection(title, pairs, trailingHtml = '') {
  const el = document.createElement('div');
  el.className = 'detail-section';
  const rows = pairs.map(([label, value]) => `
    <div class="detail-item">
      <div class="label">${escapeHtml(label)}</div>
      <div class="value">${value == null || value === '' ? '—' : escapeHtml(String(value))}</div>
    </div>
  `).join('');
  el.innerHTML = `
    <h3>${escapeHtml(title)}</h3>
    <div class="detail-grid">${rows}</div>
    ${trailingHtml}
  `;
  return el;
}

function escapeHtml(s) {
  return (s || '').replace(/[&<>"']/g, c => ({
    '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
  }[c]));
}
```

- [ ] **Step 2: Manual smoke test**

Reload. Click a parcel in the list. Confirm the right panel populates with Property Facts, Owner, Zoning, Score Breakdown stub, Financials — and the Google Maps link opens Chicago coordinates.

- [ ] **Step 3: Commit**

```bash
cd chicago-pipeline && git add webapp/static/js/detail.js
git commit -m "feat(webapp): detail panel with score breakdown stub"
```

---

## Task 13: Wire initial load + startup polish

App.js currently empty. Add a startup log and ensure the first filter fetch happens before list/map initial loads (race: filters.js resolves `/api/filters` and only then dispatches `filterchange`, which is what triggers list and map — so this already works, but let's make app.js the documented entry point and add the page title/meta bootstrap).

**Files:**
- Modify: `chicago-pipeline/webapp/static/js/app.js`

- [ ] **Step 1: Fill in app.js**

Replace `chicago-pipeline/webapp/static/js/app.js` with:

```javascript
// App entry. filters.js fires the initial 'filterchange' event once the
// filter schema has loaded; list.js and map.js listen and populate
// themselves. This file exists as the documented entry point and for
// any cross-panel wiring that doesn't belong to a specific module.

console.info('Chicago Pipeline Review UI ready.');
```

- [ ] **Step 2: Commit**

```bash
cd chicago-pipeline && git add webapp/static/js/app.js
git commit -m "chore(webapp): app.js entry-point docstring"
```

---

## Task 14: End-to-end smoke test

One pytest that starts the Flask test client against smoke.db and walks the full flow — schema → parcels list → pick one → fetch detail → fetch map.

**Files:**
- Modify: `chicago-pipeline/tests/test_webapp_routes.py`

- [ ] **Step 1: Append end-to-end test**

Append to `chicago-pipeline/tests/test_webapp_routes.py`:

```python
def test_e2e_flow_against_smoke_db(pop_client):
    # 1. Load filter schema
    filters = pop_client.get("/api/filters").get_json()
    assert len(filters["filter_groups"]) == 6

    # 2. First page of ranked list
    listing = pop_client.get("/api/parcels?limit=20").get_json()
    assert listing["total"] == 641
    first_pin = listing["parcels"][0]["pin"]

    # 3. Apply a filter and re-load
    filtered = pop_client.get("/api/parcels?is_absentee=true&limit=20").get_json()
    assert filtered["total"] == 568

    # 4. Detail for first pin includes google_maps_url + contacts array
    detail = pop_client.get(f"/api/parcels/{first_pin}").get_json()
    assert detail["pin"] == first_pin
    assert "google_maps_url" in detail
    assert detail["contacts"] == []  # smoke.db has no contacts

    # 5. Map-data under same filter returns valid GeoJSON
    geo = pop_client.get("/api/map-data?is_absentee=true").get_json()
    assert geo["type"] == "FeatureCollection"
    assert len(geo["features"]) > 0
    assert len(geo["features"]) <= 568
```

- [ ] **Step 2: Run full test suite**

Run: `cd chicago-pipeline && pytest -v`
Expected: all prior tests still pass, plus `test_e2e_flow_against_smoke_db PASSED`.

- [ ] **Step 3: Commit**

```bash
cd chicago-pipeline && git add tests/test_webapp_routes.py
git commit -m "test(webapp): end-to-end smoke flow against smoke.db"
```

---

## Task 15: README section for running the UI

Document the CLI so the user can run the UI without reading code.

**Files:**
- Modify: `chicago-pipeline/README.md`

- [ ] **Step 1: Append a Review UI section**

Append to `chicago-pipeline/README.md`:

```markdown
## Review UI

Read-only Flask app showing all parcels in the database with filter panel, map, and detail view.

```bash
# Run against smoke.db (641 parcels, default)
python -m webapp

# Run against another DB
python -m webapp --db data/full.db --port 5000
```

Open `http://127.0.0.1:5000/`.

**Scope limits:** scoring is not yet implemented — parcels are sorted by `last_updated_date` and the Score Breakdown panel is a stub. Outreach UI is hidden behind `--outreach` (not yet functional).
```

- [ ] **Step 2: Commit**

```bash
cd chicago-pipeline && git add README.md
git commit -m "docs(webapp): README Review UI section"
```

---

## Self-Review Notes

**Spec coverage (Section 4):**
- Three-column layout (list · map · detail) — Tasks 8, 9, 10, 11, 12 ✅
- Map with OpenStreetMap/CARTO tiles, color-coded pins, layer toggles — Task 11 ✅
- Right-panel property facts, owner, zoning, financials, Google Maps link — Task 12 ✅
- Score breakdown (version-stamped, config-driven) — **Stubbed per plan scope** (see Risk 1)
- Feedback Report section — **Omitted per plan scope** (see Risk 6)
- Filter panel, dynamic/config-driven (range, checkbox, dropdown, date, text) — Tasks 3, 4, 9 ✅
- Active filter count + clear all — Task 9 ✅
- Stage filter pills — Task 9 ✅
- Batch "Draft Outreach" button + Due Today banner — **Flagged off via `FEATURE_OUTREACH`** (see Risk 6)

**Placeholder scan:** No "TBD", "implement later", or "similar to Task N" references. Each code step contains actual code.

**Type/name consistency:**
- Filter state shape: `{column: True | "value" | {min, max}}` used consistently in `filters.js`, `parcel_query.py`, `_parse_filters` route parser.
- `category` values (`top`, `consolidated`, `outreach`, `listed`, `other`) match between `_map_category` (routes.py) and `CATEGORY_COLORS` (map.js).
- API response shapes match between route tests and frontend consumers (`total` + `parcels` for list, GeoJSON FeatureCollection for map).

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-20-chicago-pipeline-review-ui.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
