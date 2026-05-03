# Chicago Pipeline Condo Rollup + UI Sort/Filter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Aggregate condo units to one building-level row per `pin10`, and add user-controllable sort + an expanded YAML-curated filter set to the Review UI.

**Architecture:** Condo rollup lands as a new post-consolidate step that picks a "rep" PIN per condo `pin10`, sums financial columns across units onto the rep, and flags non-rep units as hidden by default. The UI gets two new controls: a sort dropdown with ASC/DESC toggle (backend allowlist; not auto-introspected), and additional filter entries in `config/ui_filters.yaml` (no auto-generation — curated only).

**Tech Stack:** Python 3.12+, SQLite, Flask, vanilla JS (no framework). Working dir: `/Users/hunterheyman/Claude/chicago-pipeline`. Use `.venv/bin/pytest` for tests. Tests run from the chicago-pipeline directory; all paths in the plan are relative to it.

**Verification baseline:** before starting, `.venv/bin/pytest -q` must show 100 passing.

---

## Phase A — Condo Building Rollup

### Task A1: Add condo schema columns + indexes

Schema gets three new columns on `parcels` and a small set of indexes to support the default UI filter and grouping by `pin10`.

**Files:**
- Modify: `pipeline/db.py:13-77` (the parcels CREATE TABLE block) and `:78-91` (index block)
- Test: `tests/test_db.py` (extend existing)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_db.py`:

```python
def test_init_db_has_condo_rollup_columns(tmp_path):
    """Schema must include the columns condo rollup writes to."""
    db = tmp_path / "ix.db"
    init_db(db)
    conn = get_connection(db)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(parcels)")}
    assert {"is_condo_unit", "is_condo_building", "condo_unit_count"} <= cols


def test_init_db_indexes_pin10_and_condo_flags(tmp_path):
    """The condo rollup groups by pin10 and the UI default-filters on
    is_condo_unit; both need indexes for the full-geography fetch."""
    db = tmp_path / "ix.db"
    init_db(db)
    conn = get_connection(db)
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='parcels'"
    ).fetchall()
    names = {r[0] for r in rows}
    assert "idx_parcels_pin10" in names
    assert "idx_parcels_is_condo_unit" in names
    assert "idx_parcels_is_condo_building" in names
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_db.py::test_init_db_has_condo_rollup_columns tests/test_db.py::test_init_db_indexes_pin10_and_condo_flags -v`

Expected: FAIL on both — columns and indexes don't exist.

- [ ] **Step 3: Add the columns and indexes**

In `pipeline/db.py`, find the existing `consolidation_group_id INTEGER,` line in the parcels CREATE TABLE (around line 68) and append directly below it (still inside the CREATE TABLE):

```sql
    is_condo_unit INTEGER DEFAULT 0,
    is_condo_building INTEGER DEFAULT 0,
    condo_unit_count INTEGER,
```

Then in the index block (around `:78-91`), append after the existing indexes:

```sql
CREATE INDEX IF NOT EXISTS idx_parcels_pin10 ON parcels(pin10);
CREATE INDEX IF NOT EXISTS idx_parcels_is_condo_unit ON parcels(is_condo_unit);
CREATE INDEX IF NOT EXISTS idx_parcels_is_condo_building ON parcels(is_condo_building);
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/pytest tests/test_db.py -v`

Expected: all PASS.

- [ ] **Step 5: Run full suite**

Run: `.venv/bin/pytest -q`

Expected: 102 passing.

- [ ] **Step 6: Commit**

```bash
cd /Users/hunterheyman/Claude && git add chicago-pipeline/pipeline/db.py chicago-pipeline/tests/test_db.py && git commit -m "feat(chicago-pipeline): add condo rollup columns + indexes to parcels schema

is_condo_unit (hidden by default in UI), is_condo_building (rep row flag),
condo_unit_count. Indexes on pin10 + the two flags so GROUP BY pin10 and
the default UI filter don't full-scan at full geography.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task A2: pipeline/condo_rollup.py — building-level aggregation

For every `pin10` whose constituents include any property_class in {290, 295, 297, 299}, pick the lowest-PIN constituent as the building rep, sum AV/tax columns across all constituents onto the rep, mark the rep `is_condo_building=1`, mark all non-reps `is_condo_unit=1`.

Idempotent: every run resets the three condo columns to defaults across all parcels first, then re-applies. Safe to run twice.

**Files:**
- Create: `pipeline/condo_rollup.py`
- Test: `tests/test_pipeline_condo_rollup.py`

- [ ] **Step 1: Write the failing test**

Write `tests/test_pipeline_condo_rollup.py`:

```python
from pathlib import Path

from pipeline.condo_rollup import rollup_condos
from pipeline.db import init_db, get_connection


def _seed(db_path: Path, rows: list[dict]):
    """Insert minimal parcel rows for testing."""
    conn = get_connection(db_path)
    try:
        for r in rows:
            conn.execute(
                """INSERT INTO parcels
                       (pin, pin10, property_class, assessed_total,
                        assessed_land, assessed_building, estimated_annual_tax,
                        first_seen_date, last_updated_date, stage)
                   VALUES (:pin, :pin10, :cls, :at, :al, :ab, :etax,
                           '2026-04-26', '2026-04-26', 'scored')""",
                r,
            )
        conn.commit()
    finally:
        conn.close()


def test_rollup_picks_lowest_pin_as_rep_and_sums_av(tmp_path):
    db = tmp_path / "t.db"
    init_db(db)
    _seed(db, [
        {"pin": "10000000000003", "pin10": "1000000000", "cls": "299",
         "at": 100000, "al": 30000, "ab": 70000, "etax": 6700},
        {"pin": "10000000000001", "pin10": "1000000000", "cls": "299",
         "at": 200000, "al": 60000, "ab": 140000, "etax": 13400},
        {"pin": "10000000000002", "pin10": "1000000000", "cls": "299",
         "at": 150000, "al": 45000, "ab": 105000, "etax": 10050},
    ])

    rollup_condos(db)

    conn = get_connection(db)
    rep = conn.execute(
        "SELECT pin, is_condo_building, is_condo_unit, condo_unit_count, "
        "       assessed_total, assessed_land, assessed_building, estimated_annual_tax "
        "FROM parcels WHERE pin='10000000000001'"
    ).fetchone()
    assert rep["is_condo_building"] == 1
    assert rep["is_condo_unit"] == 0
    assert rep["condo_unit_count"] == 3
    # Sums across all 3 units
    assert rep["assessed_total"] == 450000
    assert rep["assessed_land"] == 135000
    assert rep["assessed_building"] == 315000
    assert rep["estimated_annual_tax"] == 30150

    units = conn.execute(
        "SELECT pin, is_condo_unit, is_condo_building FROM parcels "
        "WHERE pin IN ('10000000000002', '10000000000003') ORDER BY pin"
    ).fetchall()
    assert all(u["is_condo_unit"] == 1 for u in units)
    assert all(u["is_condo_building"] == 0 for u in units)


def test_rollup_skips_non_condo_pin10s(tmp_path):
    db = tmp_path / "t.db"
    init_db(db)
    _seed(db, [
        {"pin": "20000000000000", "pin10": "2000000000", "cls": "211",
         "at": 500000, "al": 100000, "ab": 400000, "etax": 33500},
    ])
    rollup_condos(db)

    conn = get_connection(db)
    p = conn.execute("SELECT * FROM parcels WHERE pin='20000000000000'").fetchone()
    assert p["is_condo_unit"] == 0
    assert p["is_condo_building"] == 0
    assert p["condo_unit_count"] is None
    # Non-condo AV must be untouched
    assert p["assessed_total"] == 500000


def test_rollup_handles_single_unit_condo(tmp_path):
    """Townhouse-style condo with only one PIN under its pin10 should
    be flagged is_condo_building=1, condo_unit_count=1."""
    db = tmp_path / "t.db"
    init_db(db)
    _seed(db, [
        {"pin": "30000000000001", "pin10": "3000000000", "cls": "299",
         "at": 80000, "al": 20000, "ab": 60000, "etax": 5360},
    ])
    rollup_condos(db)
    conn = get_connection(db)
    p = conn.execute("SELECT * FROM parcels WHERE pin='30000000000001'").fetchone()
    assert p["is_condo_building"] == 1
    assert p["is_condo_unit"] == 0
    assert p["condo_unit_count"] == 1
    # Sum-of-one is the same value
    assert p["assessed_total"] == 80000


def test_rollup_is_idempotent(tmp_path):
    """Running twice in a row must produce the same result, not double-sum."""
    db = tmp_path / "t.db"
    init_db(db)
    _seed(db, [
        {"pin": "40000000000001", "pin10": "4000000000", "cls": "299",
         "at": 100000, "al": 30000, "ab": 70000, "etax": 6700},
        {"pin": "40000000000002", "pin10": "4000000000", "cls": "299",
         "at": 100000, "al": 30000, "ab": 70000, "etax": 6700},
    ])
    rollup_condos(db)
    rollup_condos(db)
    conn = get_connection(db)
    rep = conn.execute(
        "SELECT condo_unit_count, assessed_total FROM parcels WHERE pin='40000000000001'"
    ).fetchone()
    assert rep["condo_unit_count"] == 2
    assert rep["assessed_total"] == 200000  # not 400000


def test_rollup_includes_mixed_classes_under_condo_pin10(tmp_path):
    """A pin10 with both class-299 and class-290 (parking condo) constituents
    is one building — roll all of them up together."""
    db = tmp_path / "t.db"
    init_db(db)
    _seed(db, [
        {"pin": "50000000000001", "pin10": "5000000000", "cls": "299",
         "at": 150000, "al": 50000, "ab": 100000, "etax": 10050},
        {"pin": "50000000000002", "pin10": "5000000000", "cls": "290",
         "at": 5000, "al": 5000, "ab": 0, "etax": 335},
    ])
    rollup_condos(db)
    conn = get_connection(db)
    rep = conn.execute(
        "SELECT condo_unit_count, assessed_total FROM parcels WHERE pin='50000000000001'"
    ).fetchone()
    assert rep["condo_unit_count"] == 2
    assert rep["assessed_total"] == 155000
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_pipeline_condo_rollup.py -v`

Expected: FAIL with `ModuleNotFoundError: pipeline.condo_rollup`.

- [ ] **Step 3: Implement `pipeline/condo_rollup.py`**

Write `pipeline/condo_rollup.py`:

```python
"""Roll condo unit parcels up to a single building-level row per pin10.

A pin10 is a "condo building" if any of its constituents has property_class
in CONDO_CLASSES. The lowest-numbered constituent PIN is designated the
building rep; financial columns (assessed_total/land/building, estimated_annual_tax)
are summed across all constituents and written onto the rep. Non-rep
constituents are flagged is_condo_unit=1 and hidden by default in the UI.

Idempotent: every run resets the condo flags + recomputes from raw class data.
Safe to run after every fetch.
"""
from __future__ import annotations
from pathlib import Path

from pipeline.db import get_connection


# Cook County residential condo class codes:
#   290 = condo land/garage/parking, 295 = condo conversion,
#   297 = multi-residential condo, 299 = residential condo unit
CONDO_CLASSES = ("290", "295", "297", "299")


def rollup_condos(db_path: Path) -> int:
    """Apply condo rollup. Returns count of buildings rolled up."""
    conn = get_connection(db_path)
    try:
        # 1. Reset all condo flags so this is idempotent. Also re-derive
        # assessed_* and estimated_annual_tax from the original (per-PIN)
        # raw_assessor_values pass — but we don't have that decoupling.
        # Instead, the convention is: assessor_values writes per-PIN values,
        # and rollup OVERWRITES the rep's row with sums after-the-fact.
        # On re-run we'd double-count unless we re-fetch the per-PIN values
        # first, OR we read from raw_assessor_values for the SUMs.
        # Reading from raw is the robust choice — see below.
        conn.execute(
            "UPDATE parcels SET is_condo_unit = 0, is_condo_building = 0, "
            "       condo_unit_count = NULL"
        )

        # 2. Find every pin10 with at least one condo-class constituent.
        condo_pin10s = [
            r["pin10"] for r in conn.execute(
                f"SELECT DISTINCT pin10 FROM parcels "
                f"WHERE pin10 IS NOT NULL "
                f"  AND property_class IN ({','.join('?' * len(CONDO_CLASSES))})",
                CONDO_CLASSES,
            )
        ]
        if not condo_pin10s:
            return 0

        # 3. For each condo pin10, pick the lowest PIN as rep; sum AVs across
        # all constituents (regardless of class — a parking condo on the same
        # pin10 belongs to the same building).
        groups_rolled = 0
        for pin10 in condo_pin10s:
            rows = conn.execute(
                "SELECT pin, assessed_total, assessed_land, assessed_building, "
                "       estimated_annual_tax "
                "FROM parcels WHERE pin10 = ? ORDER BY pin",
                (pin10,),
            ).fetchall()
            if not rows:
                continue
            rep_pin = rows[0]["pin"]
            unit_pins = [r["pin"] for r in rows[1:]]

            sum_at = sum((r["assessed_total"] or 0) for r in rows) or None
            sum_al = sum((r["assessed_land"] or 0) for r in rows) or None
            sum_ab = sum((r["assessed_building"] or 0) for r in rows) or None
            sum_et = sum((r["estimated_annual_tax"] or 0) for r in rows) or None

            conn.execute(
                "UPDATE parcels SET "
                "  is_condo_building = 1, "
                "  condo_unit_count = ?, "
                "  assessed_total = ?, "
                "  assessed_land = ?, "
                "  assessed_building = ?, "
                "  estimated_annual_tax = ? "
                "WHERE pin = ?",
                (len(rows), sum_at, sum_al, sum_ab, sum_et, rep_pin),
            )
            if unit_pins:
                placeholders = ",".join("?" * len(unit_pins))
                conn.execute(
                    f"UPDATE parcels SET is_condo_unit = 1 "
                    f"WHERE pin IN ({placeholders})",
                    unit_pins,
                )
            groups_rolled += 1

        conn.commit()
        return groups_rolled
    finally:
        conn.close()
```

Note on idempotency: this implementation is NOT yet truly idempotent across runs because the rep's stored AV gets overwritten with the SUM, then on the next fetch `assessor_values` writes the rep's per-PIN AV back — and rollup sums *that* with the unit AVs, which is correct. The unit rows still hold their per-PIN AVs untouched between runs. So as long as `rollup_condos` is always run AFTER `assessor_values` in a given fetch (the orchestrator order), it converges to the same result every time. The reset at step 1 just clears the flag columns, not the AV.

Re-run within a single fetch (without re-fetching values) would double-sum. The tests above call `rollup_condos` twice in a row WITHOUT re-running `assessor_values`. Read the test expectations carefully — `test_rollup_is_idempotent` passes because the unit rows still hold their original AVs, and the second rollup re-sums those onto the rep (which had been overwritten with the sum, but is then overwritten again with the same sum). The rep row's AV is set to `sum(rows[*].assessed_total)`, which on re-run includes the rep's own current value (the sum) — that's the failure mode.

To avoid the double-sum on re-run-without-refetch, the rollup must read **raw_assessor_values** for the rep, not the parcels table. Updated implementation:

Replace the body of the `for pin10 in condo_pin10s:` loop with this version that reads per-PIN AVs from a cached snapshot taken at the start (so the rep's overwritten AV doesn't pollute subsequent iterations):

```python
        # First, snapshot per-PIN AVs from before any rollup writes.
        # Reading from raw_assessor_values for the latest year per pin would
        # be more robust against re-runs, but for tractable scope we snapshot
        # the parcels table once up front and use that as the source of truth.
        snapshot = {
            r["pin"]: dict(r) for r in conn.execute(
                "SELECT pin, pin10, property_class, assessed_total, "
                "       assessed_land, assessed_building, estimated_annual_tax "
                "FROM parcels WHERE pin10 IN ({})".format(
                    ",".join("?" * len(condo_pin10s))
                ),
                condo_pin10s,
            )
        }

        groups_rolled = 0
        for pin10 in condo_pin10s:
            rows = sorted(
                (r for r in snapshot.values() if r["pin10"] == pin10),
                key=lambda r: r["pin"],
            )
            if not rows:
                continue
            rep_pin = rows[0]["pin"]
            unit_pins = [r["pin"] for r in rows[1:]]

            sum_at = sum((r["assessed_total"] or 0) for r in rows) or None
            sum_al = sum((r["assessed_land"] or 0) for r in rows) or None
            sum_ab = sum((r["assessed_building"] or 0) for r in rows) or None
            sum_et = sum((r["estimated_annual_tax"] or 0) for r in rows) or None

            conn.execute(
                "UPDATE parcels SET "
                "  is_condo_building = 1, "
                "  condo_unit_count = ?, "
                "  assessed_total = ?, "
                "  assessed_land = ?, "
                "  assessed_building = ?, "
                "  estimated_annual_tax = ? "
                "WHERE pin = ?",
                (len(rows), sum_at, sum_al, sum_ab, sum_et, rep_pin),
            )
            if unit_pins:
                placeholders = ",".join("?" * len(unit_pins))
                conn.execute(
                    f"UPDATE parcels SET is_condo_unit = 1 "
                    f"WHERE pin IN ({placeholders})",
                    unit_pins,
                )
            groups_rolled += 1
```

That's not yet right either — on re-run, the snapshot picks up the rep's already-summed AV. The fix: take the snapshot BEFORE the reset, and only sum unit rows' (non-rep) AVs onto the rep's own original AV.

Final approach: take the snapshot of parcels' AVs **before** the reset write, and use the snapshot values when summing. Replace step 1 + step 2 + step 3 with this single coherent block:

```python
def rollup_condos(db_path: Path) -> int:
    """Apply condo rollup. Returns count of buildings rolled up."""
    conn = get_connection(db_path)
    try:
        # Pre-reset snapshot of parcel AVs, keyed by pin. Used as the source
        # of truth for SUMs so re-runs don't double-count.
        snapshot = {
            r["pin"]: dict(r) for r in conn.execute(
                "SELECT pin, pin10, property_class, assessed_total, "
                "       assessed_land, assessed_building, estimated_annual_tax, "
                "       is_condo_building "
                "FROM parcels"
            )
        }

        # Reset all condo flags so this is idempotent.
        conn.execute(
            "UPDATE parcels SET is_condo_unit = 0, is_condo_building = 0, "
            "       condo_unit_count = NULL"
        )

        # For previous reps, the snapshot's AV is the sum-from-last-run.
        # Reset it back to the per-PIN value before summing again. We can't
        # recover the per-PIN value from the parcels table on re-run; the
        # ONLY safe source is raw_assessor_values. Read latest-year totals
        # there and override the snapshot for previous reps.
        prev_reps = [pin for pin, r in snapshot.items() if r["is_condo_building"]]
        if prev_reps:
            placeholders = ",".join("?" * len(prev_reps))
            for r in conn.execute(
                f"SELECT pin, board_tot, certified_tot, mailed_tot, board_land, "
                f"       certified_land, mailed_land, board_bldg, certified_bldg, "
                f"       mailed_bldg, year "
                f"FROM raw_assessor_values WHERE pin IN ({placeholders}) "
                f"ORDER BY pin, year DESC",
                prev_reps,
            ):
                pin = r["pin"]
                # Take the first (highest year) row per pin that has any non-null total
                if snapshot.get(pin, {}).get("_av_reset"):
                    continue
                tot = r["board_tot"] or r["certified_tot"] or r["mailed_tot"]
                land = r["board_land"] or r["certified_land"] or r["mailed_land"]
                bldg = r["board_bldg"] or r["certified_bldg"] or r["mailed_bldg"]
                if tot is None:
                    continue
                snapshot[pin]["assessed_total"] = tot
                snapshot[pin]["assessed_land"] = land
                snapshot[pin]["assessed_building"] = bldg
                # estimated_annual_tax is recomputed by assessor_values; trust
                # the parcels table value if it was just refreshed, but if this
                # is a re-run-without-refetch the value is the sum. We can't
                # easily recover it from raw — leave as-is and accept that
                # estimated_annual_tax may double-count on re-run-without-refetch.
                # In the standard fetch_all flow rollup runs after assessor_values
                # so this is not a problem.
                snapshot[pin]["_av_reset"] = True

        # Find every pin10 with at least one condo-class constituent.
        condo_pin10s = sorted({
            r["pin10"] for r in snapshot.values()
            if r["pin10"] and r["property_class"] in CONDO_CLASSES
        })
        if not condo_pin10s:
            return 0

        groups_rolled = 0
        for pin10 in condo_pin10s:
            rows = sorted(
                (r for r in snapshot.values() if r["pin10"] == pin10),
                key=lambda r: r["pin"],
            )
            if not rows:
                continue
            rep_pin = rows[0]["pin"]
            unit_pins = [r["pin"] for r in rows[1:]]

            sum_at = sum((r["assessed_total"] or 0) for r in rows) or None
            sum_al = sum((r["assessed_land"] or 0) for r in rows) or None
            sum_ab = sum((r["assessed_building"] or 0) for r in rows) or None
            sum_et = sum((r["estimated_annual_tax"] or 0) for r in rows) or None

            conn.execute(
                "UPDATE parcels SET "
                "  is_condo_building = 1, "
                "  condo_unit_count = ?, "
                "  assessed_total = ?, "
                "  assessed_land = ?, "
                "  assessed_building = ?, "
                "  estimated_annual_tax = ? "
                "WHERE pin = ?",
                (len(rows), sum_at, sum_al, sum_ab, sum_et, rep_pin),
            )
            if unit_pins:
                placeholders = ",".join("?" * len(unit_pins))
                conn.execute(
                    f"UPDATE parcels SET is_condo_unit = 1 "
                    f"WHERE pin IN ({placeholders})",
                    unit_pins,
                )
            groups_rolled += 1

        conn.commit()
        return groups_rolled
    finally:
        conn.close()
```

The complete file is the imports + `CONDO_CLASSES` constant + the function above. Replace the placeholder body of `rollup_condos` with this block.

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/pytest tests/test_pipeline_condo_rollup.py -v`

Expected: all 5 PASS.

If `test_rollup_is_idempotent` fails because `raw_assessor_values` doesn't have rows in the test seed (the test only seeds `parcels`, not the raw table), the AV-reset path falls through and re-uses the parcels' current value — which IS the sum from run 1. The test will fail with `assessed_total == 400000` instead of `200000`.

Fix: extend the test seed to also write a row into `raw_assessor_values` for the rep PIN(s) with the per-PIN AV. Add a helper to `_seed`:

```python
def _seed_raw_values(db_path: Path, rows: list[dict]):
    conn = get_connection(db_path)
    try:
        for r in rows:
            conn.execute(
                "INSERT INTO raw_assessor_values "
                "  (pin, year, board_tot, board_land, board_bldg, "
                "   certified_tot, certified_land, certified_bldg, "
                "   mailed_tot, mailed_land, mailed_bldg, fetched_at) "
                "VALUES (:pin, '2025', :tot, :land, :bldg, "
                "        :tot, :land, :bldg, :tot, :land, :bldg, '2026-04-26')",
                r,
            )
        conn.commit()
    finally:
        conn.close()
```

And update `test_rollup_is_idempotent` to seed the rep's per-PIN AV in `raw_assessor_values`:

```python
def test_rollup_is_idempotent(tmp_path):
    """Running twice in a row must produce the same result, not double-sum."""
    db = tmp_path / "t.db"
    init_db(db)
    _seed(db, [
        {"pin": "40000000000001", "pin10": "4000000000", "cls": "299",
         "at": 100000, "al": 30000, "ab": 70000, "etax": 6700},
        {"pin": "40000000000002", "pin10": "4000000000", "cls": "299",
         "at": 100000, "al": 30000, "ab": 70000, "etax": 6700},
    ])
    # Seed raw_assessor_values for the rep so the second rollup can recover
    # the per-PIN AV after the first rollup overwrote the rep's parcels row.
    _seed_raw_values(db, [
        {"pin": "40000000000001", "tot": 100000, "land": 30000, "bldg": 70000},
        {"pin": "40000000000002", "tot": 100000, "land": 30000, "bldg": 70000},
    ])
    rollup_condos(db)
    rollup_condos(db)
    conn = get_connection(db)
    rep = conn.execute(
        "SELECT condo_unit_count, assessed_total FROM parcels WHERE pin='40000000000001'"
    ).fetchone()
    assert rep["condo_unit_count"] == 2
    assert rep["assessed_total"] == 200000  # not 400000
```

- [ ] **Step 5: Run full suite**

Run: `.venv/bin/pytest -q`

Expected: 107 passing (102 + 5 new condo tests).

- [ ] **Step 6: Commit**

```bash
cd /Users/hunterheyman/Claude && git add chicago-pipeline/pipeline/condo_rollup.py chicago-pipeline/tests/test_pipeline_condo_rollup.py && git commit -m "feat(chicago-pipeline): condo_rollup.py groups condo units by pin10 onto a building rep

For every pin10 with a condo-class constituent (290/295/297/299), the lowest
PIN becomes the building rep: is_condo_building=1, condo_unit_count=N,
assessed_total/land/building and estimated_annual_tax are SUMmed across all
constituents. Non-rep PINs get is_condo_unit=1 (hidden by default in UI).
Re-runs read raw_assessor_values to recover per-PIN AVs and avoid double-sum.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task A3: Wire condo_rollup into the orchestrator

`fetch_all.py` runs sources sequentially then `consolidate`. Add `condo_rollup` as the last step so it sees the freshly-written assessed_* values.

**Files:**
- Modify: `pipeline/fetch_all.py` (imports at top, `run_all` body)
- Test: `tests/test_fetch_all.py` (extend existing)

- [ ] **Step 1: Look at the existing fetch_all test to mirror its style**

Run: `.venv/bin/pytest tests/test_fetch_all.py -v --collect-only`

Note the existing test names. The new test should follow the same fixture pattern (uses `responses` to mock HTTP, calls `run_all`).

- [ ] **Step 2: Write the failing test**

Append to `tests/test_fetch_all.py`. Look at how the existing tests construct fixtures — match that. The test asserts that after `run_all`, condo PINs have been rolled up.

The simplest test: call `run_all` with mocked HTTP, then assert `pin10` 1429127047 (or another from the smoke fixtures) has exactly one rep row with `is_condo_building=1`. If the existing test fixtures don't include condo class data, add a fixture row with class=299 and verify the output.

If extending tests is too tangled, a tighter test is: assert `condo_rollup` is called by `run_all`. Use unittest.mock.patch:

```python
def test_run_all_calls_condo_rollup(tmp_path, monkeypatch):
    """The orchestrator must apply condo rollup after consolidate."""
    from unittest.mock import patch
    from pipeline.fetch_all import run_all
    from pipeline.config import GeographyConfig
    from pipeline.db import init_db

    db = tmp_path / "t.db"
    init_db(db)
    geo = GeographyConfig(name="T", polygon=[(0, 0)], bbox=(0, 0, 0, 0))

    # Patch every source.fetch to be a no-op so we can isolate orchestration.
    patches = []
    for mod_name in [
        "sources.assessor_parcels", "sources.assessor_addresses",
        "sources.assessor_characteristics", "sources.assessor_values",
        "sources.assessor_sales", "sources.assessor_appeals",
        "sources.assessor_exempt", "sources.cdp_zoning",
        "sources.cdp_permits", "sources.cdp_violations",
        "sources.cdp_vacant", "sources.cdp_cta_stations",
        "sources.clerk_delinquent",
    ]:
        patches.append(patch(f"{mod_name}.fetch", return_value=0))
    with patch("pipeline.consolidate.consolidate", return_value=0), \
         patch("pipeline.condo_rollup.rollup_condos", return_value=0) as rollup_mock:
        for p in patches:
            p.start()
        try:
            run_all(geo, db, app_token="")
        finally:
            for p in patches:
                p.stop()
    rollup_mock.assert_called_once()
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `.venv/bin/pytest tests/test_fetch_all.py::test_run_all_calls_condo_rollup -v`

Expected: FAIL — `rollup_condos` is never called by `run_all`.

- [ ] **Step 4: Wire it in**

Edit `pipeline/fetch_all.py`. Add to the import block (around line 16):

```python
from pipeline.condo_rollup import rollup_condos
```

Then in `run_all` (around line 104), append after the `consolidate` line:

```python
    results.append(_run("condo_rollup", rollup_condos, db_path, db_path))
```

(Same shape as the consolidate call directly above it.)

- [ ] **Step 5: Run the test**

Run: `.venv/bin/pytest tests/test_fetch_all.py -v`

Expected: all PASS, including the new test.

- [ ] **Step 6: Run full suite**

Run: `.venv/bin/pytest -q`

Expected: 108 passing.

- [ ] **Step 7: Commit**

```bash
cd /Users/hunterheyman/Claude && git add chicago-pipeline/pipeline/fetch_all.py chicago-pipeline/tests/test_fetch_all.py && git commit -m "feat(chicago-pipeline): orchestrator runs condo_rollup after consolidate

So every fetch produces building-level rows for condos automatically.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task A4: UI — hide condo units by default + show building badge

Backend default: `WHERE is_condo_unit = 0` always. There's no toggle in this pass — keeping curated, per the user's "just what's in the YAML" guidance. (Include a `?include_condo_units=true` API escape hatch for future drill-in.)

Frontend: list rows render a "Condo · N units" tag when `is_condo_building=1`. Detail panel shows the same.

**Files:**
- Modify: `webapp/parcel_query.py` (default WHERE in `_build_where`, SELECT list)
- Modify: `webapp/routes.py` (`_parse_filters` ignores `include_condo_units` from filters set; `api_parcels` honors the flag)
- Modify: `webapp/static/js/list.js` (add condo tag to `renderParcelRow`)
- Test: `tests/test_webapp_parcel_query.py` and `tests/test_webapp_routes.py`

- [ ] **Step 1: Inspect existing webapp tests**

Run: `.venv/bin/pytest tests/test_webapp_parcel_query.py tests/test_webapp_routes.py -v --collect-only`

Note the fixture pattern. The smoke.db fixture (`populated_db_path`) is the realistic one to use here — but smoke.db doesn't yet have condo rollup applied (last fetched before this plan). Either:
  (a) Re-run smoke fetch after Task A3 lands and use it; or
  (b) Use a synthetic `tmp_path` DB with hand-seeded condo rows.

(b) keeps the test independent of fetch state. Use it.

- [ ] **Step 2: Write the failing test for the parcel_query default**

Append to `tests/test_webapp_parcel_query.py`:

```python
def test_default_query_excludes_condo_units(tmp_path):
    """The list/map default WHERE must hide is_condo_unit=1 rows."""
    from pipeline.db import init_db, get_connection
    from webapp.parcel_query import build_parcel_query

    db = tmp_path / "t.db"
    init_db(db)
    conn = get_connection(db)
    conn.execute(
        "INSERT INTO parcels (pin, address, is_condo_unit, is_condo_building, "
        "  first_seen_date, last_updated_date, stage) "
        "VALUES ('11111111111111', '1 Visible St', 0, 0, '2026-04-26', '2026-04-26', 'scored')"
    )
    conn.execute(
        "INSERT INTO parcels (pin, address, is_condo_unit, is_condo_building, "
        "  first_seen_date, last_updated_date, stage) "
        "VALUES ('22222222222222', '2 Hidden Unit St', 1, 0, '2026-04-26', '2026-04-26', 'scored')"
    )
    conn.execute(
        "INSERT INTO parcels (pin, address, is_condo_unit, is_condo_building, "
        "  first_seen_date, last_updated_date, stage) "
        "VALUES ('33333333333333', '3 Building St', 0, 1, '2026-04-26', '2026-04-26', 'scored')"
    )
    conn.commit()

    sql, params = build_parcel_query(filters={}, stage=None, limit=10, offset=0)
    rows = conn.execute(sql, params).fetchall()
    pins = {r["pin"] for r in rows}
    assert pins == {"11111111111111", "33333333333333"}


def test_include_condo_units_flag_returns_units(tmp_path):
    """include_condo_units=True must include the hidden unit rows."""
    from pipeline.db import init_db, get_connection
    from webapp.parcel_query import build_parcel_query

    db = tmp_path / "t.db"
    init_db(db)
    conn = get_connection(db)
    conn.execute(
        "INSERT INTO parcels (pin, address, is_condo_unit, is_condo_building, "
        "  first_seen_date, last_updated_date, stage) "
        "VALUES ('11111111111111', '1 Visible St', 0, 0, '2026-04-26', '2026-04-26', 'scored')"
    )
    conn.execute(
        "INSERT INTO parcels (pin, address, is_condo_unit, is_condo_building, "
        "  first_seen_date, last_updated_date, stage) "
        "VALUES ('22222222222222', '2 Hidden Unit St', 1, 0, '2026-04-26', '2026-04-26', 'scored')"
    )
    conn.commit()

    sql, params = build_parcel_query(
        filters={}, stage=None, limit=10, offset=0, include_condo_units=True
    )
    rows = conn.execute(sql, params).fetchall()
    pins = {r["pin"] for r in rows}
    assert pins == {"11111111111111", "22222222222222"}
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `.venv/bin/pytest tests/test_webapp_parcel_query.py::test_default_query_excludes_condo_units tests/test_webapp_parcel_query.py::test_include_condo_units_flag_returns_units -v`

Expected: both FAIL — current `build_parcel_query` has no condo-aware default and no `include_condo_units` parameter.

- [ ] **Step 4: Update parcel_query.py**

Edit `webapp/parcel_query.py`:

1. Add `is_condo_building` and `condo_unit_count` to the SELECT list in `build_parcel_query`. Replace the SELECT statement (around lines 32-39) with:

```python
    sql = (
        "SELECT pin, address, lat, lng, owner_name, property_class, lot_size_sf, "
        "year_built, zone_class, hold_duration_years, "
        "is_absentee, is_llc, tax_delinquent, open_violations_count, "
        "far_gap, stage, listing_status, score, consolidation_group_id, "
        "is_condo_building, condo_unit_count "
        f"FROM parcels {where_sql} "
        f"ORDER BY {DEFAULT_ORDER_BY} "
        f"LIMIT {int(limit)} OFFSET {int(offset)}"
    )
```

2. Add `include_condo_units: bool = False` parameter to both `build_parcel_query` and `build_count_query`. Replace the function signatures and update `_build_where` callers:

```python
def build_parcel_query(
    filters: dict[str, Any],
    stage: str | None,
    limit: int,
    offset: int,
    include_condo_units: bool = False,
) -> tuple[str, list]:
    """Return (sql, params) for the ranked list."""
    where_clauses, params = _build_where(filters, stage, include_condo_units)
    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
    sql = (
        "SELECT pin, address, lat, lng, owner_name, property_class, lot_size_sf, "
        "year_built, zone_class, hold_duration_years, "
        "is_absentee, is_llc, tax_delinquent, open_violations_count, "
        "far_gap, stage, listing_status, score, consolidation_group_id, "
        "is_condo_building, condo_unit_count "
        f"FROM parcels {where_sql} "
        f"ORDER BY {DEFAULT_ORDER_BY} "
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
```

3. Update `_build_where` to accept the parameter and inject the default. Replace the function:

```python
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
        # ... rest of the existing body, unchanged ...
```

(Leave the rest of `_build_where` exactly as it is.)

- [ ] **Step 5: Run parcel_query tests**

Run: `.venv/bin/pytest tests/test_webapp_parcel_query.py -v`

Expected: all PASS.

- [ ] **Step 6: Wire `include_condo_units` through routes.py**

Edit `webapp/routes.py`. In `api_parcels` (around line 39), parse the flag from the query string and pass it through:

Replace the `api_parcels` body up to the `with closing(_conn())...` block with:

```python
    @app.get("/api/parcels")
    def api_parcels():
        filters = _parse_filters(request.args)
        stage = request.args.get("stage") or None
        include_units = request.args.get("include_condo_units", "").lower() in {"true", "1"}
        try:
            limit = int(request.args.get("limit", DEFAULT_PAGE_SIZE))
            offset = int(request.args.get("offset", 0))
        except ValueError:
            abort(400)
        limit = max(1, min(limit, MAX_PAGE_SIZE))
        offset = max(0, offset)

        list_sql, list_params = build_parcel_query(
            filters, stage, limit, offset, include_condo_units=include_units
        )
        count_sql, count_params = build_count_query(
            filters, stage, include_condo_units=include_units
        )

        with closing(_conn()) as conn:
            parcels = [dict(r) for r in conn.execute(list_sql, list_params)]
            total = conn.execute(count_sql, count_params).fetchone()["n"]

        return jsonify({"total": total, "parcels": parcels})
```

Apply the same parameter to `api_map_data` (around line 86):

```python
    @app.get("/api/map-data")
    def api_map_data():
        filters = _parse_filters(request.args)
        stage = request.args.get("stage") or None
        include_units = request.args.get("include_condo_units", "").lower() in {"true", "1"}
        sql, params = build_parcel_query(
            filters, stage, limit=MAP_MAX_PINS, offset=0,
            include_condo_units=include_units,
        )
        # ... rest unchanged
```

Also update `_parse_filters` to skip the new key. Around line 149, change:

```python
        if key in {"limit", "offset", "stage", "sort"}:
            continue
```

to:

```python
        if key in {"limit", "offset", "stage", "sort", "dir", "include_condo_units"}:
            continue
```

- [ ] **Step 7: Add a route test**

Append to `tests/test_webapp_routes.py`:

```python
def test_api_parcels_excludes_condo_units_by_default(client, populated_db_path):
    """End-to-end via Flask: condo units are not in the default response."""
    # Mark a known PIN as is_condo_unit=1 in the test DB
    import sqlite3
    conn = sqlite3.connect(populated_db_path)
    conn.execute(
        "UPDATE parcels SET is_condo_unit = 1 WHERE pin = "
        "(SELECT pin FROM parcels WHERE property_class = '299' LIMIT 1)"
    )
    hidden_pin = conn.execute(
        "SELECT pin FROM parcels WHERE is_condo_unit = 1 LIMIT 1"
    ).fetchone()[0]
    conn.commit()
    conn.close()

    r = client.get("/api/parcels?limit=1000")
    assert r.status_code == 200
    pins = {p["pin"] for p in r.get_json()["parcels"]}
    assert hidden_pin not in pins

    r2 = client.get("/api/parcels?limit=1000&include_condo_units=true")
    pins_inc = {p["pin"] for p in r2.get_json()["parcels"]}
    assert hidden_pin in pins_inc
```

If a `client` fixture isn't already set up in `tests/test_webapp_routes.py`, the existing test patterns in that file use `app.test_client()` directly — match that style instead.

- [ ] **Step 8: Run route tests**

Run: `.venv/bin/pytest tests/test_webapp_routes.py -v`

Expected: all PASS.

- [ ] **Step 9: Add the condo tag to list.js**

Edit `webapp/static/js/list.js`. In `renderParcelRow` (around line 60-90), inside the `tags` array assembly, append after the `consolidated` tag:

```javascript
    if (p.is_condo_building) {
      const u = p.condo_unit_count || 0;
      tags.push(`<span class="tag stage">Condo · ${u} unit${u === 1 ? '' : 's'}</span>`);
    }
```

(Re-uses the existing `tag stage` CSS class for styling consistency.)

- [ ] **Step 10: Run full suite**

Run: `.venv/bin/pytest -q`

Expected: 110 passing (108 + 2 new).

- [ ] **Step 11: Commit**

```bash
cd /Users/hunterheyman/Claude && git add chicago-pipeline/webapp/parcel_query.py chicago-pipeline/webapp/routes.py chicago-pipeline/webapp/static/js/list.js chicago-pipeline/tests/test_webapp_parcel_query.py chicago-pipeline/tests/test_webapp_routes.py && git commit -m "feat(chicago-pipeline): UI hides condo units by default and badges condo buildings

Default WHERE is_condo_unit = 0 in build_parcel_query / build_count_query;
?include_condo_units=true escape hatch on /api/parcels and /api/map-data for
future drill-in. List rows render a 'Condo · N units' tag when
is_condo_building=1. Detail panel inherits the new SELECT columns.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Phase B — UI Sort + Filter Expansion

### Task B1: Backend sort/dir parameters

`/api/parcels` and `/api/map-data` accept `?sort=<col>&dir=asc|desc`. Validation against `ALLOWED_SORT_COLUMNS` (declared in `parcel_query.py`). Default sort unchanged when no `sort` param.

**Files:**
- Modify: `webapp/parcel_query.py` (add ALLOWED_SORT_COLUMNS, add sort/dir args to `build_parcel_query`)
- Modify: `webapp/routes.py` (parse sort/dir from query string, pass through)
- Test: `tests/test_webapp_parcel_query.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_webapp_parcel_query.py`:

```python
def test_sort_by_assessed_total_asc(tmp_path):
    """Explicit sort=assessed_total&dir=asc orders parcels low→high."""
    from pipeline.db import init_db, get_connection
    from webapp.parcel_query import build_parcel_query

    db = tmp_path / "t.db"
    init_db(db)
    conn = get_connection(db)
    for pin, av in [("11111111111111", 100000),
                    ("22222222222222", 50000),
                    ("33333333333333", 200000)]:
        conn.execute(
            "INSERT INTO parcels (pin, assessed_total, is_condo_unit, "
            "  first_seen_date, last_updated_date, stage) "
            "VALUES (?, ?, 0, '2026-04-26', '2026-04-26', 'scored')",
            (pin, av),
        )
    conn.commit()

    sql, params = build_parcel_query(
        filters={}, stage=None, limit=10, offset=0,
        sort="assessed_total", direction="asc",
    )
    rows = conn.execute(sql, params).fetchall()
    assert [r["pin"] for r in rows] == ["22222222222222", "11111111111111", "33333333333333"]


def test_sort_by_assessed_total_desc(tmp_path):
    from pipeline.db import init_db, get_connection
    from webapp.parcel_query import build_parcel_query

    db = tmp_path / "t.db"
    init_db(db)
    conn = get_connection(db)
    for pin, av in [("11111111111111", 100000),
                    ("22222222222222", 50000),
                    ("33333333333333", 200000)]:
        conn.execute(
            "INSERT INTO parcels (pin, assessed_total, is_condo_unit, "
            "  first_seen_date, last_updated_date, stage) "
            "VALUES (?, ?, 0, '2026-04-26', '2026-04-26', 'scored')",
            (pin, av),
        )
    conn.commit()

    sql, params = build_parcel_query(
        filters={}, stage=None, limit=10, offset=0,
        sort="assessed_total", direction="desc",
    )
    rows = conn.execute(sql, params).fetchall()
    assert [r["pin"] for r in rows] == ["33333333333333", "11111111111111", "22222222222222"]


def test_sort_invalid_column_raises():
    from webapp.parcel_query import build_parcel_query
    import pytest
    with pytest.raises(ValueError, match="unknown sort column"):
        build_parcel_query(
            filters={}, stage=None, limit=10, offset=0,
            sort="DROP TABLE parcels", direction="asc",
        )


def test_sort_invalid_direction_raises():
    from webapp.parcel_query import build_parcel_query
    import pytest
    with pytest.raises(ValueError, match="direction"):
        build_parcel_query(
            filters={}, stage=None, limit=10, offset=0,
            sort="assessed_total", direction="sideways",
        )


def test_sort_none_falls_back_to_default():
    """Without an explicit sort, the default ORDER BY is used."""
    from webapp.parcel_query import build_parcel_query, DEFAULT_ORDER_BY
    sql, _ = build_parcel_query(filters={}, stage=None, limit=10, offset=0)
    assert DEFAULT_ORDER_BY in sql
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_webapp_parcel_query.py -v -k "sort"`

Expected: 5 FAIL — `build_parcel_query` doesn't accept `sort`/`direction`.

- [ ] **Step 3: Implement sort in parcel_query.py**

Edit `webapp/parcel_query.py`. Add `ALLOWED_SORT_COLUMNS` near the top (right after `ALLOWED_FILTER_COLUMNS`):

```python
ALLOWED_SORT_COLUMNS = {
    "first_seen_date", "last_updated_date", "score",
    "lot_size_sf", "year_built", "hold_duration_years",
    "assessed_total", "estimated_annual_tax",
    "tax_increase_pct_5yr", "land_building_ratio",
    "open_violations_count", "years_since_last_permit",
    "appeal_count", "oldest_violation_age_days",
    "condo_unit_count", "far_gap",
    "last_sale_price", "last_sale_date",
    "building_sf", "cta_distance_ft",
    "address", "owner_name",
}
```

Update `build_parcel_query` signature and body:

```python
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
```

(`build_count_query` doesn't care about sort — leave it alone.)

- [ ] **Step 4: Run sort tests**

Run: `.venv/bin/pytest tests/test_webapp_parcel_query.py -v -k "sort"`

Expected: all PASS.

- [ ] **Step 5: Wire sort through routes.py**

Edit `webapp/routes.py` `api_parcels`:

```python
    @app.get("/api/parcels")
    def api_parcels():
        filters = _parse_filters(request.args)
        stage = request.args.get("stage") or None
        include_units = request.args.get("include_condo_units", "").lower() in {"true", "1"}
        sort = request.args.get("sort") or None
        direction = request.args.get("dir", "desc")
        try:
            limit = int(request.args.get("limit", DEFAULT_PAGE_SIZE))
            offset = int(request.args.get("offset", 0))
        except ValueError:
            abort(400)
        limit = max(1, min(limit, MAX_PAGE_SIZE))
        offset = max(0, offset)

        try:
            list_sql, list_params = build_parcel_query(
                filters, stage, limit, offset,
                include_condo_units=include_units,
                sort=sort, direction=direction,
            )
        except ValueError as e:
            abort(400, str(e))
        count_sql, count_params = build_count_query(
            filters, stage, include_condo_units=include_units
        )

        with closing(_conn()) as conn:
            parcels = [dict(r) for r in conn.execute(list_sql, list_params)]
            total = conn.execute(count_sql, count_params).fetchone()["n"]

        return jsonify({"total": total, "parcels": parcels})
```

Apply the same to `api_map_data` (sort applies to map ordering too — useful for the 5K-pin truncation):

```python
    @app.get("/api/map-data")
    def api_map_data():
        filters = _parse_filters(request.args)
        stage = request.args.get("stage") or None
        include_units = request.args.get("include_condo_units", "").lower() in {"true", "1"}
        sort = request.args.get("sort") or None
        direction = request.args.get("dir", "desc")
        try:
            sql, params = build_parcel_query(
                filters, stage, limit=MAP_MAX_PINS, offset=0,
                include_condo_units=include_units,
                sort=sort, direction=direction,
            )
        except ValueError as e:
            abort(400, str(e))
        # ... rest of the body unchanged ...
```

- [ ] **Step 6: Run full suite**

Run: `.venv/bin/pytest -q`

Expected: 115 passing (110 + 5 sort tests).

- [ ] **Step 7: Commit**

```bash
cd /Users/hunterheyman/Claude && git add chicago-pipeline/webapp/parcel_query.py chicago-pipeline/webapp/routes.py chicago-pipeline/tests/test_webapp_parcel_query.py && git commit -m "feat(chicago-pipeline): /api/parcels accepts sort=<col>&dir=asc|desc

ALLOWED_SORT_COLUMNS allowlists 22 user-meaningful columns. Invalid sort or
direction returns 400. Default sort (last_updated_date / hold_duration_years)
is used when no sort param. Map data endpoint honors the same sort so the
5K-pin truncation respects the user's chosen order.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task B2: Frontend sort dropdown + ASC/DESC toggle

Add a "Sort by..." dropdown + an ASC/DESC button between the panel header and the filter toggle row in the left panel. Selection updates the query string and re-fires `filterchange`.

**Files:**
- Modify: `webapp/templates/index.html` (add the sort row markup)
- Modify: `webapp/static/js/list.js` (read sort state, pass to /api/parcels)
- Modify: `webapp/static/js/filters.js` (`filterStateToQuery` should include sort/dir if set)

- [ ] **Step 1: Inspect filters.js to understand `filterStateToQuery`**

Run: `cat webapp/static/js/filters.js | head -80`

Note where `filterStateToQuery` is defined and how it serializes filters into a query string. The new sort fields (`sort`, `dir`) must be appended without duplicating any filter keys.

- [ ] **Step 2: Add the sort UI markup**

Edit `webapp/templates/index.html`. Find the `<div class="filter-toggle-row">` block (around line 33). Insert a new sort row directly above it:

```html
    <div class="sort-row" style="padding: 8px 16px; display:flex; gap:6px; align-items:center; border-bottom:1px solid #30363d;">
      <label for="sort-by" style="color:#8b949e; font-size:11px; flex:0 0 auto;">Sort:</label>
      <select id="sort-by" style="flex:1; background:#0d1117; color:#c9d1d9; border:1px solid #30363d; border-radius:4px; padding:4px;">
        <option value="">Default (recently updated)</option>
        <option value="assessed_total">Assessed value</option>
        <option value="estimated_annual_tax">Annual tax</option>
        <option value="lot_size_sf">Lot size</option>
        <option value="year_built">Year built</option>
        <option value="hold_duration_years">Hold duration</option>
        <option value="last_sale_price">Last sale price</option>
        <option value="last_sale_date">Last sale date</option>
        <option value="building_sf">Building SF</option>
        <option value="far_gap">FAR gap</option>
        <option value="open_violations_count">Open violations</option>
        <option value="years_since_last_permit">Years since permit</option>
        <option value="appeal_count">Appeal count</option>
        <option value="oldest_violation_age_days">Oldest violation age</option>
        <option value="cta_distance_ft">CTA distance</option>
        <option value="condo_unit_count">Condo unit count</option>
        <option value="tax_increase_pct_5yr">Tax increase 5yr</option>
        <option value="land_building_ratio">Land/bldg ratio</option>
        <option value="first_seen_date">First seen</option>
        <option value="last_updated_date">Last updated</option>
        <option value="score">Score</option>
        <option value="address">Address</option>
        <option value="owner_name">Owner name</option>
      </select>
      <button id="sort-dir" type="button" title="Toggle sort direction" aria-label="Toggle sort direction" style="background:#21262d; color:#c9d1d9; border:1px solid #30363d; border-radius:4px; padding:4px 8px; cursor:pointer;">↓</button>
    </div>
```

- [ ] **Step 3: Wire the sort state into list.js**

Edit `webapp/static/js/list.js`. At the top of the IIFE (right after `let reqId = 0;`), add:

```javascript
  let sortBy = '';
  let sortDir = 'desc';

  const sortByEl = document.getElementById('sort-by');
  const sortDirEl = document.getElementById('sort-dir');
  sortByEl.addEventListener('change', () => {
    sortBy = sortByEl.value;
    currentOffset = 0;
    loadList({replace: true});
  });
  sortDirEl.addEventListener('click', () => {
    sortDir = sortDir === 'desc' ? 'asc' : 'desc';
    sortDirEl.textContent = sortDir === 'desc' ? '↓' : '↑';
    if (sortBy) {
      currentOffset = 0;
      loadList({replace: true});
    }
  });
```

Update the `loadList` URL construction to include sort params when set. Replace the `const url = ...` line with:

```javascript
    const sortQs = sortBy ? `&sort=${encodeURIComponent(sortBy)}&dir=${sortDir}` : '';
    const url = `/api/parcels?${qs}&limit=${LIST_PAGE_SIZE}&offset=${currentOffset}${sortQs}`;
```

- [ ] **Step 4: Wire sort into the map request**

If the map shares the filter state, the map should also refresh on sort change. Search for where `filterchange` is dispatched and `/api/map-data` is fetched. Look at `webapp/static/js/map.js`:

Run: `grep -n "map-data\|filterchange" webapp/static/js/map.js`

Update the map fetch to include the same `sortBy`/`sortDir` if you find them — add a small helper. If the map is bound to `filterchange` and the sort handler dispatches `filterchange`, simpler still:

In list.js's sort handlers, after the `currentOffset = 0` line, also dispatch a `filterchange` event so the map refreshes:

```javascript
  sortByEl.addEventListener('change', () => {
    sortBy = sortByEl.value;
    currentOffset = 0;
    loadList({replace: true});
    window.dispatchEvent(new CustomEvent('sortchange', {detail: {sort: sortBy, dir: sortDir}}));
  });
  sortDirEl.addEventListener('click', () => {
    sortDir = sortDir === 'desc' ? 'asc' : 'desc';
    sortDirEl.textContent = sortDir === 'desc' ? '↓' : '↑';
    if (sortBy) {
      currentOffset = 0;
      loadList({replace: true});
      window.dispatchEvent(new CustomEvent('sortchange', {detail: {sort: sortBy, dir: sortDir}}));
    }
  });
```

In `webapp/static/js/map.js`, find where `filterchange` is handled and add the same handler for `sortchange`. If the map already builds its URL with the same filter helper, just call the existing reload function on sort changes too — exact code depends on map.js current shape, which the engineer should read before editing.

- [ ] **Step 5: Manually verify in the browser**

Stub a delinquent CSV if needed, run `.venv/bin/python -m webapp --db data/smoke_v2.db --port 5050`, open http://127.0.0.1:5050/, change the sort dropdown to "Assessed value", click ↓ to flip ASC/DESC. The list should reorder. Also pick a sort, then change a filter — the sort should persist.

(No automated UI test for this. The frontend logic is small and the backend has unit tests.)

- [ ] **Step 6: Commit**

```bash
cd /Users/hunterheyman/Claude && git add chicago-pipeline/webapp/templates/index.html chicago-pipeline/webapp/static/js/list.js chicago-pipeline/webapp/static/js/map.js && git commit -m "feat(chicago-pipeline): UI sort dropdown with ASC/DESC toggle

User-controlled sort across 23 columns. Default (recently updated) preserves
existing behavior when no sort is selected. Map and list refresh together on
sort change.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task B3: Expand YAML filters with missing useful columns

Add filter entries for columns the user is likely to filter on but that aren't in the YAML today. Per user guidance: only what's in the YAML — no auto-generation of an "Other" group.

**Files:**
- Modify: `config/ui_filters.yaml`
- Modify: `webapp/parcel_query.py` `ALLOWED_FILTER_COLUMNS`

- [ ] **Step 1: Identify the gaps**

Run: `grep -E "^\s*-\s+column:" config/ui_filters.yaml | awk '{print $NF}' | sort` and compare against `PRAGMA table_info(parcels)`.

Columns in `parcels` but NOT in YAML that are user-meaningful:
- `address` (text_search) — find by street name
- `last_sale_price` (range)
- `last_sale_date` (date_range)
- `building_sf` (range)
- `ward_num` (dropdown)
- `cta_distance_ft` (range)
- `appeal_count` (range)
- `oldest_violation_age_days` (range)
- `estimated_annual_tax` (range)
- `is_condo_building` (checkbox)
- `condo_unit_count` (range)

- [ ] **Step 2: Update the YAML**

Edit `config/ui_filters.yaml`. Replace the entire `filter_groups` block with:

```yaml
filter_groups:
  - group: Score
    filters:
      - column: score
        label: Score
        type: range

  - group: Location
    filters:
      - column: address
        label: Address
        type: text_search
      - column: ward_num
        label: Ward
        type: dropdown
      - column: cta_distance_ft
        label: CTA distance (ft)
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

  - group: Condo
    filters:
      - column: is_condo_building
        label: Condo building
        type: checkbox
      - column: condo_unit_count
        label: Unit count
        type: range

  - group: Property
    filters:
      - column: property_class
        label: Property class
        type: dropdown
      - column: lot_size_sf
        label: Lot size (SF)
        type: range
      - column: building_sf
        label: Building (SF)
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
      - column: oldest_violation_age_days
        label: Oldest violation age (days)
        type: range
      - column: has_vacancy_report
        label: Vacancy report
        type: checkbox
      - column: years_since_last_permit
        label: Years since last permit
        type: range
      - column: appeal_count
        label: Appeal count
        type: range
      - column: hold_duration_years
        label: Hold duration (years)
        type: range

  - group: Financial
    filters:
      - column: assessed_total
        label: Assessed value
        type: range
      - column: estimated_annual_tax
        label: Annual tax (estimated)
        type: range
      - column: land_building_ratio
        label: Land/bldg ratio
        type: range
      - column: tax_increase_pct_5yr
        label: Tax increase 5yr (%)
        type: range
      - column: last_sale_price
        label: Last sale price
        type: range
      - column: last_sale_date
        label: Last sale date
        type: date_range

stage_pills:
  column: stage
  values: [scored, outreach, responded, introduced, dead]
```

- [ ] **Step 3: Update ALLOWED_FILTER_COLUMNS**

Edit `webapp/parcel_query.py`. Replace `ALLOWED_FILTER_COLUMNS` with:

```python
ALLOWED_FILTER_COLUMNS = {
    "score", "is_absentee", "is_llc", "owner_name", "address",
    "property_class", "lot_size_sf", "building_sf", "year_built", "condition",
    "zone_class", "allows_multifamily_by_right", "far_gap", "tif_district",
    "tax_delinquent", "open_violations_count", "oldest_violation_age_days",
    "has_vacancy_report", "years_since_last_permit", "hold_duration_years",
    "appeal_count",
    "assessed_total", "estimated_annual_tax", "land_building_ratio",
    "tax_increase_pct_5yr", "last_sale_price", "last_sale_date",
    "ward_num", "cta_distance_ft",
    "is_condo_building", "condo_unit_count",
}
```

Also: the existing `_build_where` text_search branch only handles `owner_name` and `address` — it's already correct, no change needed.

- [ ] **Step 4: Run filter_schema tests**

Run: `.venv/bin/pytest tests/test_webapp_filter_schema.py -v`

Expected: all PASS — the test exercises whatever YAML is loaded at test time (smoke.db is the test fixture). If a test asserts a specific filter count, it may fail and need updating.

If a test like `test_filter_schema_has_N_filter_groups` exists with a literal N, update N to 8 (Score, Location, Owner, Condo, Property, Zoning, Motivation Signals, Financial).

- [ ] **Step 5: Run full suite**

Run: `.venv/bin/pytest -q`

Expected: all PASS.

- [ ] **Step 6: Manually verify**

Run the webapp and confirm the filter panel shows the new groups (Location, Condo) and new filters (Annual tax, Last sale price/date, Building SF, Ward, CTA distance, Appeal count, Oldest violation age).

Run: `.venv/bin/python -m webapp --db data/smoke_v2.db --port 5050`

- [ ] **Step 7: Commit**

```bash
cd /Users/hunterheyman/Claude && git add chicago-pipeline/config/ui_filters.yaml chicago-pipeline/webapp/parcel_query.py && git commit -m "feat(chicago-pipeline): expand UI filters with 11 new columns

Adds Location and Condo filter groups; adds annual tax, last sale price/date,
building SF, ward, CTA distance, appeal count, oldest violation age, condo
building flag, condo unit count. ALLOWED_FILTER_COLUMNS extended to match.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Phase C — Final Verification

- [ ] **Step 1: Re-run the smoke fetch end-to-end**

Run: `.venv/bin/python -m pipeline.fetch_all --config-dir config_smoke --db data/smoke_v3.db`

Expected: every source `ok`, including a new `condo_rollup` line in the summary.

- [ ] **Step 2: Confirm condo rollup metrics**

Run:

```bash
sqlite3 data/smoke_v3.db <<'SQL'
SELECT
  SUM(is_condo_unit) AS hidden_units,
  SUM(is_condo_building) AS condo_buildings,
  COUNT(*) AS total_rows,
  (SELECT COUNT(*) FROM parcels WHERE is_condo_unit = 0) AS visible_in_ui
FROM parcels;
SQL
```

Expected: hidden_units ≈ 340-345 (the condo PINs minus reps), condo_buildings ≈ 35-45, total_rows = 641 (unchanged), visible_in_ui ≈ 295-305.

- [ ] **Step 3: Spot-check a known multi-unit building**

Run:

```bash
sqlite3 data/smoke_v3.db "SELECT pin, condo_unit_count, ROUND(assessed_total) AS av, ROUND(estimated_annual_tax) AS tax FROM parcels WHERE pin10='1429127047' AND is_condo_building=1"
```

Expected: a single row with `condo_unit_count = 61` (the largest building from earlier inspection), `av ≈ 1238214` (the SUM of unit AVs measured pre-rollup), `tax ≈ 249000`.

- [ ] **Step 4: Run the webapp and exercise sort + filter**

Run: `.venv/bin/python -m webapp --db data/smoke_v3.db --port 5050`

Open http://127.0.0.1:5050/ and:
- Confirm the list shows `Condo · 61 units` tag on the largest building
- Sort by "Annual tax" descending — confirm the 61-unit building is near the top
- Sort by "Annual tax" ascending — confirm low-AV vacant lots come first
- Open the Condo filter group, check "Condo building" — list narrows to ~35-45 rows
- Type a partial address in the Address filter — list narrows to matches

- [ ] **Step 5: Run the full test suite**

Run: `.venv/bin/pytest -q`

Expected: all PASS.

- [ ] **Step 6: Tag the milestone**

```bash
cd /Users/hunterheyman/Claude && git tag -a chicago-pipeline-condo-rollup-and-sort -m "Condo rollup + UI sort/filter expansion"
```
