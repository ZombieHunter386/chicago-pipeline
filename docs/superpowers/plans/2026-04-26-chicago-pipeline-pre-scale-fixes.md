# Chicago Pipeline Pre-Scale Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land all data-correctness and scale-durability fixes identified in the 2026-04-26 audit so a full-geography fetch produces clean, complete data without timing out.

**Architecture:** Two phases. Phase 1 is correctness (fix every column where the population logic produces wrong/null values at smoke scale + add the missing `estimated_annual_tax` column). Phase 2 is scale durability (rewrite the O(N×M) spatial loops, add indexes, harden Socrata pagination). Each task ships as a separate commit with new tests.

**Tech Stack:** Python 3.12+, SQLite, requests, geopandas/shapely, pytest with `responses` for HTTP mocking. Working dir: `/Users/hunterheyman/Claude/chicago-pipeline`. All code paths in this plan are relative to that directory unless noted.

**Verification baseline:** before starting, run `pytest -q` from `chicago-pipeline/` — must show 76 passing. After each task, all tests must still pass. After Phase 1, re-run the smoke fetch (`python -m pipeline.fetch_all --config-dir config_smoke --db data/smoke_v2.db`) and confirm column population improves on the documented metrics.

---

## Phase 1 — Correctness

### Task 1: Fix the `assessor_values` "current = latest year" trap

`sources/assessor_values.py:64-80` hard-pins `current = rows[0]` (latest year), then reads `current["board_tot"]`. The latest tax year (currently 2026) has Board-of-Review values not yet published — `board_tot` is NULL, cascading to `assessed_total`, `assessed_land`, `assessed_building`, `land_building_ratio`, and the trend columns. 568 of 641 smoke parcels are NULL on `assessed_total` because of this.

Fix: pick `current` as the latest row that has at least one of `board_tot`, `certified_tot`, or `mailed_tot` populated. Use the same precedence (`board_tot OR certified_tot OR mailed_tot`) when reading any individual field. Apply the same fallback to the prior-year row used for the 1-year trend, and to the lookup for the 5-year trend.

**Files:**
- Modify: `sources/assessor_values.py:54-98`
- Test: `tests/test_source_assessor_values.py` (extend existing)
- Fixtures: `tests/fixtures/assessor_values.json` (extend existing — add a row with NULL board_tot in the latest year)

- [ ] **Step 1: Inspect the existing fixture so the new test pins to real data shape**

Run: `cat tests/fixtures/assessor_values.json | head -50`

Note the existing PIN, year format, and how `board_tot`/`certified_tot`/`mailed_tot` are populated.

- [ ] **Step 2: Extend the fixture with a "current year empty" case**

Add a new PIN whose latest year (e.g. 2026) has `board_tot=null`, `certified_tot=null`, `mailed_tot=null`, and prior years (2025, 2024, 2023) populated with `board_tot` values that exercise both the 1-yr and 5-yr trend math. Edit `tests/fixtures/assessor_values.json` to include rows like:

```json
{"pin": "14210010030000", "year": "2026",
 "board_bldg": null, "board_land": null, "board_tot": null,
 "certified_bldg": null, "certified_land": null, "certified_tot": null,
 "mailed_bldg": null, "mailed_land": null, "mailed_tot": null},
{"pin": "14210010030000", "year": "2025",
 "board_bldg": "60000", "board_land": "40000", "board_tot": "100000",
 "certified_bldg": "60000", "certified_land": "40000", "certified_tot": "100000",
 "mailed_bldg": "60000", "mailed_land": "40000", "mailed_tot": "100000"},
{"pin": "14210010030000", "year": "2024",
 "board_bldg": "55000", "board_land": "37000", "board_tot": "92000",
 "certified_bldg": "55000", "certified_land": "37000", "certified_tot": "92000",
 "mailed_bldg": "55000", "mailed_land": "37000", "mailed_tot": "92000"},
{"pin": "14210010030000", "year": "2020",
 "board_bldg": "44000", "board_land": "30000", "board_tot": "74000",
 "certified_bldg": "44000", "certified_land": "30000", "certified_tot": "74000",
 "mailed_bldg": "44000", "mailed_land": "30000", "mailed_tot": "74000"}
```

You will also need a row in `tests/fixtures/assessor_parcels.json` for PIN `14210010030000` (lat/lng inside the geo polygon: e.g. `41.93, -87.65`).

- [ ] **Step 3: Write the failing test**

Append to `tests/test_source_assessor_values.py`:

```python
@responses.activate
def test_values_falls_back_when_latest_year_is_empty(db_path, geo, cook_client):
    """Latest tax year (2026) has Board-of-Review NULL because BOR hasn't published yet.
    The pipeline must fall back to certified_tot or mailed_tot, and if all three are
    NULL on the latest year, use the latest year that has a populated value."""
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
        SELECT assessed_total, assessed_land, assessed_building,
               tax_increase_pct_1yr, tax_increase_pct_5yr
        FROM parcels WHERE pin='14210010030000'
    """).fetchone()
    # Latest non-null year is 2025 with board_tot=100000
    assert p["assessed_total"] == 100000.0
    assert p["assessed_land"] == 40000.0
    assert p["assessed_building"] == 60000.0
    # 1-yr trend: 2025 (100000) vs 2024 (92000) = ~8.7%
    assert round(p["tax_increase_pct_1yr"], 1) == 8.7
    # 5-yr trend: 2025 (100000) vs 2020 (74000) = ~35.1%
    assert round(p["tax_increase_pct_5yr"], 1) == 35.1
```

- [ ] **Step 4: Run the test to verify it fails**

Run: `pytest tests/test_source_assessor_values.py::test_values_falls_back_when_latest_year_is_empty -v`

Expected: FAIL with `assessed_total` is None (because `current["board_tot"]` is None on the 2026 row).

- [ ] **Step 5: Implement the fallback logic**

Replace lines 62-98 of `sources/assessor_values.py` with:

```python
    def _pick(row):
        """Return (total, land, bldg) using board → certified → mailed precedence."""
        if row["board_tot"] is not None:
            return row["board_tot"], row["board_land"], row["board_bldg"]
        if row["certified_tot"] is not None:
            return row["certified_tot"], row["certified_land"], row["certified_bldg"]
        if row["mailed_tot"] is not None:
            return row["mailed_tot"], row["mailed_land"], row["mailed_bldg"]
        return None, None, None

    conn = get_connection(db_path)
    try:
        for pin, rows in by_pin.items():
            # `current` = latest row whose total is populated by any source
            current = next((r for r in rows if _pick(r)[0] is not None), None)
            if current is None:
                continue
            assessed_total, assessed_land, assessed_bldg = _pick(current)
            ratio = (assessed_land / assessed_total) if (assessed_land and assessed_total) else None

            # 1-year: prior year (any year strictly older than current) with a populated value
            current_year = int(current["year"]) if current["year"] else None
            prior = next(
                (r for r in rows
                 if r["year"] and current_year and int(r["year"]) < current_year
                 and _pick(r)[0] is not None),
                None,
            )
            inc_1yr = None
            if prior:
                prior_tot, _, _ = _pick(prior)
                inc_1yr = (assessed_total / prior_tot - 1) * 100

            inc_5yr = None
            if current_year is not None:
                target_year = current_year - 5
                old = next(
                    (r for r in rows
                     if r["year"] and int(r["year"]) <= target_year
                     and _pick(r)[0] is not None),
                    None,
                )
                if old:
                    old_tot, _, _ = _pick(old)
                    inc_5yr = (assessed_total / old_tot - 1) * 100

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

- [ ] **Step 6: Run all assessor_values tests**

Run: `pytest tests/test_source_assessor_values.py -v`

Expected: both the original test and the new test PASS.

- [ ] **Step 7: Run the full suite to ensure no regression**

Run: `pytest -q`

Expected: 77 passing (was 76 before, now +1).

- [ ] **Step 8: Commit**

```bash
git add sources/assessor_values.py tests/test_source_assessor_values.py tests/fixtures/assessor_values.json tests/fixtures/assessor_parcels.json
git commit -m "fix: assessor_values falls back through certified/mailed when board_tot is null

The latest tax year publishes board_tot last; pinning to rows[0] left 568/641 smoke
parcels with NULL assessed_total. Now picks the latest row with any non-null total
and uses board → certified → mailed precedence per row, including the prior-year
and 5-year-back rows used for trend calculations."
```

---

### Task 2: Verify and fix zoning dataset id, fail-loud on empty

`raw_cdp_zoning` has 0 rows in smoke.db. `sources/cdp_zoning.py:15` declares `DATASET_ID = "7cve-jgbp"`, which is not the current Chicago Data Portal zoning districts dataset. The known live id is `nik2-fmkk` ("Boundaries — Zoning Districts"). Even with a correct id, an empty fetch should error rather than silently returning 0 — there is no scenario where a valid zoning fetch returns no polygons for a Chicago geography.

**Files:**
- Modify: `sources/cdp_zoning.py:15`, fetch function tail
- Test: `tests/test_source_cdp_zoning.py` (extend existing)

- [ ] **Step 1: Verify the live zoning dataset id**

Run:

```bash
curl -s "https://data.cityofchicago.org/resource/nik2-fmkk.json?$limit=1" | head -50
```

Expected: a JSON array with one row containing `zone_class`, `the_geom`, etc. Confirm that `zone_class` is present.

If `nik2-fmkk` returns an empty array or error, search the Chicago Data Portal for "zoning districts" and use the dataset id of the active "Boundaries — Zoning Districts" map layer instead. Document the chosen id in a one-line comment above the constant.

- [ ] **Step 2: Update the dataset id**

Edit `sources/cdp_zoning.py:15`:

```python
# Chicago Data Portal: Boundaries — Zoning Districts
DATASET_ID = "nik2-fmkk"
```

(or whichever id was confirmed in Step 1).

- [ ] **Step 3: Write the failing test for fail-loud behavior**

Append to `tests/test_source_cdp_zoning.py`:

```python
@responses.activate
def test_cdp_zoning_raises_when_dataset_returns_empty(db_path, geo, cook_client, cdp_client):
    """An empty zoning response is never legitimate for a Chicago geography.
    Surface the failure rather than silently NULLing every zoning column."""
    pf = json.loads((FIXTURES / "assessor_parcels.json").read_text())
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_parcels.DATASET_ID}.json",
        json=pf, status=200)
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_parcels.DATASET_ID}.json",
        json=[], status=200)
    assessor_parcels.fetch(geo, db_path, cook_client)

    # Empty zoning response
    responses.add(responses.GET,
        f"https://data.cityofchicago.org/resource/{cdp_zoning.DATASET_ID}.json",
        json=[], status=200)

    import pytest
    with pytest.raises(RuntimeError, match="returned 0 polygons"):
        cdp_zoning.fetch(geo, db_path, cdp_client)
```

- [ ] **Step 4: Run the test to verify it fails**

Run: `pytest tests/test_source_cdp_zoning.py::test_cdp_zoning_raises_when_dataset_returns_empty -v`

Expected: FAIL — currently `fetch` returns 0 silently.

- [ ] **Step 5: Add the fail-loud guard**

Edit `sources/cdp_zoning.py`. Replace the body around lines 49-52 (where `n = upsert_rows(...)` is followed by `if not polys: return n`) with:

```python
    n = upsert_rows(db_path, TABLE, raw_rows, key_columns=["objectid"])

    if not polys:
        raise RuntimeError(
            f"cdp_zoning: dataset {DATASET_ID} returned 0 polygons. "
            f"Verify the dataset id is current and the Socrata endpoint is reachable."
        )

    zones_gdf = gpd.GeoDataFrame(polys, crs="EPSG:4326")
```

- [ ] **Step 6: Run the test suite**

Run: `pytest tests/test_source_cdp_zoning.py -v`

Expected: original test PASS (real fixture has polygons) + new test PASS.

- [ ] **Step 7: Run the full suite**

Run: `pytest -q`

Expected: 78 passing.

- [ ] **Step 8: Commit**

```bash
git add sources/cdp_zoning.py tests/test_source_cdp_zoning.py
git commit -m "fix: cdp_zoning uses live dataset id and fails loud on empty response

Old id 7cve-jgbp returned 0 polygons silently, leaving every parcel with NULL
zone_class/max_far/far_gap. Switched to nik2-fmkk (Boundaries - Zoning Districts)
and now raises RuntimeError if the fetch returns no polygons, since that is
never legitimate for a Chicago geography."
```

---

### Task 3: Fix `is_absentee` USPS suffix + unit normalization

`sources/assessor_addresses.py:32-43` does whitespace+upper only. False-positive rate is ~89% on smoke. Fix: extract a canonical `(number, direction, street, suffix)` tuple from each address and compare those, dropping unit numbers and post-suffix tokens.

**Files:**
- Modify: `sources/assessor_addresses.py` (replace `_norm_addr`/`is_absentee` with a tuple-based comparison)
- Test: `tests/test_source_assessor_addresses.py` (extend existing)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_source_assessor_addresses.py`:

```python
def test_is_absentee_handles_pkwy_pky_suffix_variants():
    from sources.assessor_addresses import is_absentee
    # Confirmed false-positive case from smoke.db pin 14292270501001
    assert is_absentee("1122 W DIVERSEY PKY 1E", "1122 W DIVERSEY PKWY1E") is False
    assert is_absentee("1122 W DIVERSEY PKY 2E", "1122 W DIVERSEY PKWY2E") is False


def test_is_absentee_handles_full_suffix_words():
    from sources.assessor_addresses import is_absentee
    assert is_absentee("100 W DIVERSEY AVE", "100 W DIVERSEY AVENUE") is False
    assert is_absentee("100 W STATE ST", "100 W STATE STREET") is False
    assert is_absentee("100 N CLARK BLVD", "100 N CLARK BOULEVARD") is False


def test_is_absentee_handles_direction_variants():
    from sources.assessor_addresses import is_absentee
    assert is_absentee("100 NORTH STATE ST", "100 N STATE ST") is False
    assert is_absentee("100 W DIVERSEY AVE", "100 WEST DIVERSEY AVE") is False


def test_is_absentee_strips_unit_markers():
    from sources.assessor_addresses import is_absentee
    assert is_absentee("100 W DIVERSEY UNIT 5", "100 W DIVERSEY") is False
    assert is_absentee("100 W DIVERSEY APT 5", "100 W DIVERSEY UNIT 6") is False
    assert is_absentee("100 W DIVERSEY STE 200", "100 W DIVERSEY") is False
    assert is_absentee("100 W DIVERSEY #5", "100 W DIVERSEY") is False


def test_is_absentee_still_detects_real_absentee():
    from sources.assessor_addresses import is_absentee
    # PO BOX vs street → true
    assert is_absentee("1222 W DIVERSEY PKWY", "PO BOX 4421") is True
    # Different building number → true
    assert is_absentee("100 W DIVERSEY AVE", "200 W DIVERSEY AVE") is True
    # Different street → true
    assert is_absentee("100 W DIVERSEY AVE", "100 W ARMITAGE AVE") is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_source_assessor_addresses.py -v -k "pkwy or suffix_words or direction or unit_markers or still_detects"`

Expected: 4 FAIL (the existing naive normalization treats every variation as different), 1 PASS (`still_detects`).

- [ ] **Step 3: Implement canonical street-key comparison**

Replace the `_norm_addr` and `is_absentee` block in `sources/assessor_addresses.py:32-43` with:

```python
SUFFIX_MAP = {
    "AVENUE": "AVE", "AV": "AVE", "AVE": "AVE",
    "BOULEVARD": "BLVD", "BL": "BLVD", "BLVD": "BLVD",
    "PARKWAY": "PKWY", "PKY": "PKWY", "PKWY": "PKWY",
    "STREET": "ST", "ST": "ST",
    "ROAD": "RD", "RD": "RD",
    "DRIVE": "DR", "DR": "DR",
    "LANE": "LN", "LN": "LN",
    "COURT": "CT", "CT": "CT",
    "PLACE": "PL", "PL": "PL",
    "TERRACE": "TER", "TER": "TER",
    "HIGHWAY": "HWY", "HWY": "HWY",
    "PLAZA": "PLZ", "PLZ": "PLZ",
    "SQUARE": "SQ", "SQ": "SQ",
    "WAY": "WAY",
}
DIRECTION_MAP = {
    "NORTH": "N", "N": "N",
    "SOUTH": "S", "S": "S",
    "EAST": "E", "E": "E",
    "WEST": "W", "W": "W",
    "NORTHEAST": "NE", "NE": "NE",
    "NORTHWEST": "NW", "NW": "NW",
    "SOUTHEAST": "SE", "SE": "SE",
    "SOUTHWEST": "SW", "SW": "SW",
}
SUFFIX_TOKENS = set(SUFFIX_MAP.values())
UNIT_MARKER_RE = re.compile(r"\b(UNIT|APT|APARTMENT|STE|SUITE)\s+\S+", re.IGNORECASE)
HASH_UNIT_RE = re.compile(r"#\s*\S+")


def _street_key(addr: str | None) -> str | None:
    """Canonicalize an address to '<number> <dir> <street...> <suffix>'.
    Drops unit numbers (UNIT 5, APT 3, #4, trailing 1E/2W condo tokens).
    Returns None for empty input. Returns a non-suffixed key (best effort) for
    PO BOX-style addresses so they still compare correctly against street form."""
    if not addr:
        return None
    s = re.sub(r"\s+", " ", addr).strip().upper()
    # Strip explicit unit markers first so they don't muddy tokenization
    s = UNIT_MARKER_RE.sub(" ", s)
    s = HASH_UNIT_RE.sub(" ", s)
    # Insert space at letter→digit and digit→letter boundaries so PKWY1E → PKWY 1 E
    s = re.sub(r"([A-Z])(\d)", r"\1 \2", s)
    s = re.sub(r"(\d)([A-Z])", r"\1 \2", s)
    s = re.sub(r"[.,]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    if not s:
        return None
    tokens = [DIRECTION_MAP.get(t, SUFFIX_MAP.get(t, t)) for t in s.split()]
    # Truncate after the last suffix token (drops trailing unit tokens like "1 E")
    last_suffix = -1
    for i, t in enumerate(tokens):
        if t in SUFFIX_TOKENS:
            last_suffix = i
    if last_suffix >= 0:
        tokens = tokens[:last_suffix + 1]
    return " ".join(tokens) or None


def is_absentee(prop_addr: str | None, mail_addr: str | None) -> bool:
    p = _street_key(prop_addr)
    m = _street_key(mail_addr)
    if p is None or m is None:
        return False
    return p != m
```

- [ ] **Step 4: Run all assessor_addresses tests**

Run: `pytest tests/test_source_assessor_addresses.py -v`

Expected: all PASS, including the existing `test_is_absentee_normalizes_addresses` (the new code is a strict superset of the old behavior on those cases).

- [ ] **Step 5: Run the full suite**

Run: `pytest -q`

Expected: 83 passing (was 78, +5 new).

- [ ] **Step 6: Commit**

```bash
git add sources/assessor_addresses.py tests/test_source_assessor_addresses.py
git commit -m "fix: is_absentee compares canonical street keys instead of raw strings

The previous whitespace+upper compare flagged 89% of smoke parcels absentee due
to PKY/PKWY suffix variants and unit-number formatting (1E vs PKWY1E). Now extracts
a canonical (number, direction, street, suffix) tuple, drops unit markers, and
compares those — owner-occupied condos no longer false-positive as absentee."
```

---

### Task 4: Fix `is_llc` to check both owner and mail name fields

`sources/assessor_addresses.py:89` only checks `is_llc(r["mail_address_name"])`. LLC entries where the legal owner is the LLC and the mail recipient is a person (e.g. `2846 NORTH RACINE LLC` / `JENNIFER CULL`) come through as `is_llc=0`. Both fields need to be checked.

**Files:**
- Modify: `sources/assessor_addresses.py:89`
- Test: `tests/test_source_assessor_addresses.py` (extend) and `tests/fixtures/assessor_addresses.json` (extend)

- [ ] **Step 1: Inspect the fixture**

Run: `cat tests/fixtures/assessor_addresses.json`

Note: the existing fixture has both `owner_address_name` and `mail_address_name`.

- [ ] **Step 2: Add a fixture row that exercises the bug**

Append to `tests/fixtures/assessor_addresses.json` a new row for a PIN that exists in `assessor_parcels.json` but isn't already in `assessor_addresses.json`. If a free PIN doesn't exist, add one to both fixtures (`assessor_parcels.json` needs lat/lng inside the polygon: `41.93, -87.65`):

```json
{"pin": "14210010040000",
 "prop_address_full": "2846 N RACINE AVE",
 "mail_address_name": "JENNIFER CULL",
 "mail_address_full": "500 N STATE ST",
 "owner_address_name": "2846 NORTH RACINE LLC",
 "owner_address_full": "2846 N RACINE AVE"}
```

- [ ] **Step 3: Write the failing test**

Append to `tests/test_source_assessor_addresses.py`:

```python
@responses.activate
def test_is_llc_checks_owner_field_when_mail_is_a_person(db_path, geo, cook_client):
    """LLC ownership often shows on the owner_address_name line while
    mail_address_name is a person (e.g. property manager). Must flag is_llc=1."""
    parcels_fixture = json.loads((FIXTURES / "assessor_parcels.json").read_text())
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_parcels.DATASET_ID}.json",
        json=parcels_fixture, status=200)
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_parcels.DATASET_ID}.json",
        json=[], status=200)
    assessor_parcels.fetch(geo, db_path, cook_client)

    addr_fixture = json.loads((FIXTURES / "assessor_addresses.json").read_text())
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_addresses.DATASET_ID}.json",
        json=addr_fixture, status=200)
    responses.add(responses.GET,
        f"https://datacatalog.cookcountyil.gov/resource/{assessor_addresses.DATASET_ID}.json",
        json=[], status=200)
    assessor_addresses.fetch(geo, db_path, cook_client)

    conn = get_connection(db_path)
    p = conn.execute(
        "SELECT is_llc, owner_name FROM parcels WHERE pin='14210010040000'"
    ).fetchone()
    assert p["is_llc"] == 1
    assert "LLC" in (p["owner_name"] or "")
```

- [ ] **Step 4: Run the test to verify it fails**

Run: `pytest tests/test_source_assessor_addresses.py::test_is_llc_checks_owner_field_when_mail_is_a_person -v`

Expected: FAIL with `is_llc == 0`.

- [ ] **Step 5: Apply the one-line fix**

Edit `sources/assessor_addresses.py:89`:

```python
            llc = 1 if (is_llc(r["owner_address_name"]) or is_llc(r["mail_address_name"])) else 0
```

- [ ] **Step 6: Run all tests**

Run: `pytest tests/test_source_assessor_addresses.py -v`

Expected: all PASS.

- [ ] **Step 7: Run full suite**

Run: `pytest -q`

Expected: 84 passing.

- [ ] **Step 8: Commit**

```bash
git add sources/assessor_addresses.py tests/test_source_assessor_addresses.py tests/fixtures/assessor_addresses.json tests/fixtures/assessor_parcels.json
git commit -m "fix: is_llc checks both owner_address_name and mail_address_name

Many LLC-owned parcels list the LLC on owner_address_name and a person on
mail_address_name (e.g. property manager). Checking only the mail field
missed cases like 2846 NORTH RACINE LLC, IMAGIT HOLDINGS LLC, etc."
```

---

### Task 5: Make `clerk_delinquent` fail-loud when CSV is missing

`sources/clerk_delinquent.py:32-34` warns and returns 0 when `data/delinquent.csv` is absent — silently leaving `tax_delinquent` NULL on every parcel. With no Socrata fallback for this source, the user must provide the CSV manually; the fetch should refuse to run rather than producing a partial dataset.

**Files:**
- Modify: `sources/clerk_delinquent.py:30-34`
- Test: `tests/test_source_clerk_delinquent.py` (extend existing)
- Add: `README.md` snippet documenting where to get the CSV

- [ ] **Step 1: Write the failing test**

Append to `tests/test_source_clerk_delinquent.py`:

```python
def test_clerk_delinquent_raises_when_csv_missing(db_path, tmp_path):
    """Missing CSV must raise — silently skipping leaves every parcel NULL on
    tax_delinquent and produces a fetch run that looks successful but has no
    delinquency data at all."""
    import pytest
    missing = tmp_path / "does_not_exist.csv"
    with pytest.raises(FileNotFoundError, match="delinquent CSV"):
        clerk_delinquent.fetch_from_csv(missing, db_path)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_source_clerk_delinquent.py::test_clerk_delinquent_raises_when_csv_missing -v`

Expected: FAIL — current code prints and returns 0.

- [ ] **Step 3: Apply the fix**

Replace `sources/clerk_delinquent.py:32-34` with:

```python
    if not csv_path.exists():
        raise FileNotFoundError(
            f"clerk_delinquent: delinquent CSV not found at {csv_path}. "
            f"Download the latest tax-delinquent parcel list from the Cook County "
            f"Clerk and save it to {csv_path} before running the pipeline."
        )
```

- [ ] **Step 4: Run the test**

Run: `pytest tests/test_source_clerk_delinquent.py -v`

Expected: all PASS (existing tests use real CSVs, new test triggers the raise).

- [ ] **Step 5: Document the CSV source in the README**

Read `README.md` to find an appropriate "Setup" or "Data sources" section. Add (or extend existing section):

```markdown
### Cook County Clerk delinquent tax CSV

This source is not on Socrata. Download the current delinquent-tax parcel list
from the Cook County Clerk's office and save it to `data/delinquent.csv`. The
pipeline will refuse to run until this file is in place. Required columns:
`pin`, `tax_year`, `amount_owed`. PINs may be in dashed (`14-21-001-001-0000`)
or undashed form.
```

- [ ] **Step 6: Run full suite**

Run: `pytest -q`

Expected: 85 passing.

- [ ] **Step 7: Commit**

```bash
git add sources/clerk_delinquent.py tests/test_source_clerk_delinquent.py README.md
git commit -m "fix: clerk_delinquent fails loud when CSV is missing

Silently skipping left every parcel NULL on tax_delinquent and made successful
fetch runs indistinguishable from runs with no delinquency data. Now raises
FileNotFoundError with an actionable message and the README documents where
to get the CSV."
```

---

### Task 6: Implement `estimated_annual_tax` calculation

`estimated_annual_tax` is in the schema but no source writes to it — column is dead. Implement it using Cook County's standard formula: `EAV = assessed_total × equalizer; tax = max(0, EAV − exemption_eav) × composite_rate`. Use a single composite rate per Chicago (configurable per tax year) and apply the standard $10,000 EAV reduction for parcels flagged as having a Homeowner Exemption.

**Files:**
- Create: `pipeline/tax.py`
- Create: `config/tax_constants.yaml`
- Modify: `sources/assessor_values.py` (call into `pipeline.tax` after computing assessed_total)
- Test: `tests/test_pipeline_tax.py` (new) and extension of `tests/test_source_assessor_values.py`

- [ ] **Step 1: Create the config file**

Write `config/tax_constants.yaml`:

```yaml
# Cook County / Chicago property tax estimation constants.
# Update these annually as IDOR publishes the equalizer and the Cook County
# Clerk publishes composite tax rates.
#
# Sources:
#   - State equalization factor: Illinois Department of Revenue
#     https://tax.illinois.gov/localgovernments/property/equalization.html
#   - Composite tax rate: Cook County Clerk's Office, "Tax Rate Reports"
#     (Chicago city-wide weighted average for residential property)

tax_year: 2024
equalizer: 3.0027
composite_rate_pct: 6.717
homeowner_exemption_eav_reduction: 10000
```

- [ ] **Step 2: Create the tax module with a failing test first**

Write `tests/test_pipeline_tax.py`:

```python
from pathlib import Path
import pytest
from pipeline.tax import estimate_annual_tax, load_tax_constants


def test_estimate_basic_no_exemption():
    # AV 30000 × eq 3.0027 = 90,081 EAV
    # 90,081 × 6.717% = 6,049.74
    tax = estimate_annual_tax(
        assessed_total=30000,
        equalizer=3.0027,
        composite_rate_pct=6.717,
        homeowner_exemption_eav_reduction=10000,
        has_homeowner_exemption=False,
    )
    assert round(tax, 2) == 6049.74


def test_estimate_with_homeowner_exemption():
    # EAV 90,081 − 10,000 = 80,081 taxable
    # 80,081 × 6.717% = 5,378.04
    tax = estimate_annual_tax(
        assessed_total=30000,
        equalizer=3.0027,
        composite_rate_pct=6.717,
        homeowner_exemption_eav_reduction=10000,
        has_homeowner_exemption=True,
    )
    assert round(tax, 2) == 5378.04


def test_estimate_returns_none_for_null_av():
    assert estimate_annual_tax(None, 3.0, 6.7, 10000, False) is None
    assert estimate_annual_tax(0, 3.0, 6.7, 10000, False) is None


def test_estimate_floors_at_zero_when_exemption_exceeds_eav():
    # tiny AV, big exemption → tax floors at 0
    tax = estimate_annual_tax(
        assessed_total=1000,
        equalizer=3.0,
        composite_rate_pct=6.7,
        homeowner_exemption_eav_reduction=10000,
        has_homeowner_exemption=True,
    )
    assert tax == 0


def test_load_tax_constants_validates_required_keys(tmp_path):
    p = tmp_path / "incomplete.yaml"
    p.write_text("equalizer: 3.0\n")
    with pytest.raises(KeyError, match="composite_rate_pct"):
        load_tax_constants(p)


def test_load_tax_constants_returns_all_keys(tmp_path):
    p = tmp_path / "ok.yaml"
    p.write_text(
        "tax_year: 2024\n"
        "equalizer: 3.0027\n"
        "composite_rate_pct: 6.717\n"
        "homeowner_exemption_eav_reduction: 10000\n"
    )
    c = load_tax_constants(p)
    assert c["equalizer"] == 3.0027
    assert c["composite_rate_pct"] == 6.717
    assert c["homeowner_exemption_eav_reduction"] == 10000
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_pipeline_tax.py -v`

Expected: FAIL with `ModuleNotFoundError: pipeline.tax`.

- [ ] **Step 4: Implement `pipeline/tax.py`**

Write `pipeline/tax.py`:

```python
"""Property tax estimation for Cook County / Chicago parcels.

Cook County tax math:
    EAV          = assessed_total × state_equalizer
    taxable_eav  = max(0, EAV − exemption_eav_reduction)
    annual_tax   = taxable_eav × (composite_rate_pct / 100)

The composite_rate_pct is a city-wide residential average; per-parcel rates
vary by tax code (school/park/library/etc. district combination) and can be
refined later by joining the Cook County Clerk's tax-rate-by-tax-code dataset.
For now we use a single configurable rate, hence "estimated".
"""
from __future__ import annotations
from pathlib import Path

import yaml


REQUIRED_KEYS = ("equalizer", "composite_rate_pct", "homeowner_exemption_eav_reduction")


def load_tax_constants(path: Path) -> dict:
    """Load and validate tax_constants.yaml. Raises KeyError if a required
    key is missing — fail-loud is preferable to silently using zeros."""
    data = yaml.safe_load(path.read_text()) or {}
    for key in REQUIRED_KEYS:
        if key not in data:
            raise KeyError(f"{path} missing required key: {key}")
    return data


def estimate_annual_tax(
    assessed_total: float | None,
    equalizer: float,
    composite_rate_pct: float,
    homeowner_exemption_eav_reduction: float,
    has_homeowner_exemption: bool,
) -> float | None:
    """Return the estimated annual property-tax bill in dollars, or None if
    assessed_total is missing or non-positive."""
    if not assessed_total or assessed_total <= 0:
        return None
    eav = assessed_total * equalizer
    if has_homeowner_exemption:
        eav -= homeowner_exemption_eav_reduction
    if eav < 0:
        eav = 0
    return round(eav * (composite_rate_pct / 100), 2)
```

- [ ] **Step 5: Run the tax module tests**

Run: `pytest tests/test_pipeline_tax.py -v`

Expected: all 6 PASS.

- [ ] **Step 6: Wire `estimate_annual_tax` into the values fetcher**

The Homeowner Exemption signal lives in `raw_assessor_exempt`. Inspect the data:

Run: `sqlite3 data/smoke.db "SELECT DISTINCT exemption_type FROM raw_assessor_exempt LIMIT 20"`

Note the exact strings used (e.g. `Homeowner Exemption`, `HOMEOWNER`, etc.) — you'll need them for the case-insensitive substring match below.

Modify `sources/assessor_values.py`. Add to the imports at the top:

```python
from pipeline.config import CONFIG_DIR
from pipeline.tax import estimate_annual_tax, load_tax_constants
```

Add a helper near `_f`:

```python
def _has_homeowner_exemption(conn, pin: str) -> bool:
    """Look up whether this PIN has a homeowner exemption recorded.
    Match is case-insensitive substring 'homeowner' in exemption_type."""
    row = conn.execute(
        "SELECT exemption_type FROM raw_assessor_exempt WHERE pin = ?",
        (pin,),
    ).fetchone()
    if not row or not row["exemption_type"]:
        return False
    return "HOMEOWNER" in row["exemption_type"].upper()
```

In the body of `fetch`, just before opening the final `conn = get_connection(db_path)` block (i.e. before the loop that writes to parcels), load the constants:

```python
    tax_constants = load_tax_constants(CONFIG_DIR / "tax_constants.yaml")
```

Inside the loop, after computing `assessed_total`, compute and write `estimated_annual_tax`:

```python
            est_tax = estimate_annual_tax(
                assessed_total=assessed_total,
                equalizer=tax_constants["equalizer"],
                composite_rate_pct=tax_constants["composite_rate_pct"],
                homeowner_exemption_eav_reduction=tax_constants["homeowner_exemption_eav_reduction"],
                has_homeowner_exemption=_has_homeowner_exemption(conn, pin),
            )
```

Update the UPDATE statement to write `estimated_annual_tax`:

```python
            conn.execute("""
                UPDATE parcels SET
                    assessed_land = :al,
                    assessed_building = :ab,
                    assessed_total = :at,
                    land_building_ratio = :ratio,
                    estimated_annual_tax = :etax,
                    tax_increase_pct_1yr = :i1,
                    tax_increase_pct_5yr = :i5,
                    last_updated_date = :now
                WHERE pin = :pin
            """, {"al": assessed_land, "ab": assessed_bldg, "at": assessed_total,
                  "ratio": ratio, "etax": est_tax, "i1": inc_1yr, "i5": inc_5yr,
                  "now": fetched_at, "pin": pin})
```

- [ ] **Step 7: Verify CONFIG_DIR resolves correctly in tests**

Read `pipeline/config.py` to confirm `CONFIG_DIR` points to the project's `config/` folder. If tests need to override it (because they run from `tmp_path`), add a `tax_constants_path` argument to `assessor_values.fetch` defaulting to `CONFIG_DIR / "tax_constants.yaml"`. Otherwise leave as-is.

Run: `pytest tests/test_source_assessor_values.py -v`

If it fails because the test fixture doesn't include exempt-table rows: that's fine — `_has_homeowner_exemption` returns False when no row exists, so the existing tests still see consistent estimated_annual_tax values.

- [ ] **Step 8: Add a test for the wired-in calculation**

Append to `tests/test_source_assessor_values.py`:

```python
@responses.activate
def test_values_writes_estimated_annual_tax(db_path, geo, cook_client):
    """End-to-end: after assessor_values runs, estimated_annual_tax should
    be populated using the tax constants and the assessed_total derived above."""
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
        SELECT assessed_total, estimated_annual_tax FROM parcels
        WHERE pin='14210010010000'
    """).fetchone()
    # assessed_total = 287430
    # No homeowner exemption seeded → full tax
    # 287430 × 3.0027 = 862,866 EAV
    # 862,866 × 6.717% = 57,956.71
    assert p["assessed_total"] == 287430.0
    assert round(p["estimated_annual_tax"], 0) == 57957
```

- [ ] **Step 9: Run all tests**

Run: `pytest tests/test_pipeline_tax.py tests/test_source_assessor_values.py -v`

Expected: all PASS.

- [ ] **Step 10: Run full suite**

Run: `pytest -q`

Expected: 92 passing (was 85, +6 tax module tests +1 wired-in test).

- [ ] **Step 11: Commit**

```bash
git add pipeline/tax.py config/tax_constants.yaml sources/assessor_values.py tests/test_pipeline_tax.py tests/test_source_assessor_values.py
git commit -m "feat: compute estimated_annual_tax from AV, equalizer, composite rate

Implements Cook County's standard tax estimation: EAV = AV × equalizer; tax =
max(0, EAV − homeowner_exemption_eav_reduction) × composite_rate_pct. Constants
live in config/tax_constants.yaml and are refreshed annually. Homeowner Exemption
is detected by substring match on raw_assessor_exempt.exemption_type. Computed
inline with assessed_total in the values fetcher so both update atomically."
```

---

### Phase 1 Verification Checkpoint

Before starting Phase 2, re-run the smoke fetch end-to-end and confirm the column-population improvements.

- [ ] **Step 1: Provide a delinquent CSV (or skip the source)**

Either save a current Cook County delinquent CSV to `data/delinquent.csv` (real run) or for the verification fetch only, write a one-line stub:

```bash
printf "pin,tax_year,amount_owed\n" > data/delinquent.csv
```

(empty data, valid headers — fetch will run and update zero parcels but won't raise.)

- [ ] **Step 2: Run the smoke fetch to a fresh DB**

Run: `python -m pipeline.fetch_all --config-dir config_smoke --db data/smoke_v2.db`

Expected: every source reports `ok` with non-zero rows for assessor_parcels, assessor_addresses, assessor_characteristics, assessor_values, assessor_sales, assessor_appeals, assessor_exempt, **cdp_zoning (must be > 0)**, cdp_permits, cdp_violations, cdp_cta_stations.

- [ ] **Step 3: Compare population rates against the audit baseline**

Run:

```bash
sqlite3 data/smoke_v2.db <<'SQL'
SELECT
  ROUND(100.0 * SUM(assessed_total IS NOT NULL) / COUNT(*), 1) AS pct_assessed_total,
  ROUND(100.0 * SUM(estimated_annual_tax IS NOT NULL) / COUNT(*), 1) AS pct_est_tax,
  ROUND(100.0 * SUM(zone_class IS NOT NULL) / COUNT(*), 1) AS pct_zone_class,
  ROUND(100.0 * SUM(is_absentee = 1) / COUNT(*), 1) AS pct_absentee,
  ROUND(100.0 * SUM(is_llc = 1) / COUNT(*), 1) AS pct_llc,
  COUNT(*) AS total
FROM parcels;
SQL
```

Expected vs baseline:
- `pct_assessed_total`: was ~11%, target > 80%
- `pct_est_tax`: was 0%, target > 80%
- `pct_zone_class`: was 0%, target > 90%
- `pct_absentee`: was 89% (false-positive heavy), target lower (likely 30-60% in Lincoln Park)
- `pct_llc`: was modest, target higher (more correct field coverage)

If any column is regressed or unexpectedly empty, stop and diagnose before continuing to Phase 2.

- [ ] **Step 4: Spot-check the known-bad rows**

Run:

```bash
sqlite3 data/smoke_v2.db "SELECT pin, address, mail_address, is_absentee FROM parcels WHERE pin IN ('14292270501001', '14292270501003', '14291310400000')"
```

Expected:
- `14292270501001` and `14292270501003` (PKWY1E / PKWY2E false positives): now `is_absentee=0`
- `14291310400000` (true absentee with Estate Dr mail): still `is_absentee=1`

---

## Phase 2 — Scale Durability

### Task 7: Vectorize permits/violations/vacant spatial match

`sources/cdp_permits.py:73-86`, `cdp_violations.py:71-91`, and `cdp_vacant.py:75-85` each do a pure-Python O(parcels × records) haversine inner loop. At 280K parcels × 250K violations this won't complete in reasonable time. Replace with `geopandas.sjoin_nearest` against a planar projection (EPSG:3435, NAD83 Illinois East in US survey feet) so the 50ft radius is honored exactly without spherical math.

**Files:**
- Create: `pipeline/spatial.py` (shared helper)
- Modify: `sources/cdp_permits.py`, `sources/cdp_violations.py`, `sources/cdp_vacant.py`
- Test: `tests/test_pipeline_spatial.py` (new), existing tests for each source must still pass

- [ ] **Step 1: Write the failing test for the shared helper**

Write `tests/test_pipeline_spatial.py`:

```python
from pipeline.spatial import match_records_to_parcels


def test_match_returns_index_to_pin_within_radius():
    parcels = [
        {"pin": "A", "lat": 41.93000, "lng": -87.65000},
        {"pin": "B", "lat": 41.93010, "lng": -87.65000},  # ~36ft north of A
    ]
    records = [
        {"latitude": 41.93001, "longitude": -87.65000},   # ~3.6ft from A
        {"latitude": 41.93010, "longitude": -87.65000},   # 0ft from B
        {"latitude": 41.94000, "longitude": -87.65000},   # ~3600ft, no match
    ]
    result = match_records_to_parcels(records, parcels, radius_ft=50.0)
    assert result == {0: "A", 1: "B"}


def test_match_handles_records_with_no_lat_lng():
    parcels = [{"pin": "A", "lat": 41.93, "lng": -87.65}]
    records = [
        {"latitude": None, "longitude": None},
        {"latitude": 41.93, "longitude": -87.65},
    ]
    result = match_records_to_parcels(records, parcels, radius_ft=50.0)
    assert result == {1: "A"}


def test_match_returns_empty_when_no_parcels():
    assert match_records_to_parcels([{"latitude": 41.9, "longitude": -87.6}], [], 50.0) == {}


def test_match_returns_empty_when_no_records():
    assert match_records_to_parcels([], [{"pin": "A", "lat": 41.9, "lng": -87.6}], 50.0) == {}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_pipeline_spatial.py -v`

Expected: FAIL with `ModuleNotFoundError: pipeline.spatial`.

- [ ] **Step 3: Implement `pipeline/spatial.py`**

Write `pipeline/spatial.py`:

```python
"""Vectorized spatial matching of point records to parcels."""
from __future__ import annotations
from typing import Sequence

import geopandas as gpd
from shapely.geometry import Point


# NAD83 Illinois East (US survey feet) — planar CRS suited to Cook County so
# the 50ft radius is honored exactly without spherical-distance approximations.
PLANAR_CRS = "EPSG:3435"


def match_records_to_parcels(
    records: Sequence[dict],
    parcels: Sequence,
    radius_ft: float,
    record_lat_field: str = "latitude",
    record_lng_field: str = "longitude",
    parcel_pin_field: str = "pin",
    parcel_lat_field: str = "lat",
    parcel_lng_field: str = "lng",
) -> dict[int, str]:
    """Match each record (by index) to its nearest parcel within radius_ft.

    Returns: {record_index: pin}. Records with missing lat/lng are skipped.
    Records with no parcel within radius_ft are not included in the result.
    """
    if not records or not parcels:
        return {}

    parcel_features = [
        {parcel_pin_field: p[parcel_pin_field],
         "geometry": Point(p[parcel_lng_field], p[parcel_lat_field])}
        for p in parcels
        if p[parcel_lat_field] is not None and p[parcel_lng_field] is not None
    ]
    if not parcel_features:
        return {}
    parcels_gdf = gpd.GeoDataFrame(parcel_features, crs="EPSG:4326").to_crs(PLANAR_CRS)

    record_features = []
    for i, r in enumerate(records):
        lat = r.get(record_lat_field)
        lng = r.get(record_lng_field)
        if lat is None or lng is None:
            continue
        record_features.append({"_idx": i, "geometry": Point(lng, lat)})
    if not record_features:
        return {}
    records_gdf = gpd.GeoDataFrame(record_features, crs="EPSG:4326").to_crs(PLANAR_CRS)

    joined = gpd.sjoin_nearest(
        records_gdf, parcels_gdf,
        how="inner", max_distance=radius_ft, distance_col="_dist",
    )
    # If a record ties at exactly the same distance to two parcels, sjoin_nearest
    # returns both rows; keep the first.
    joined = joined.drop_duplicates(subset=["_idx"], keep="first")
    return {int(row["_idx"]): row[parcel_pin_field] for _, row in joined.iterrows()}
```

- [ ] **Step 4: Run the spatial tests**

Run: `pytest tests/test_pipeline_spatial.py -v`

Expected: all 4 PASS.

- [ ] **Step 5: Refactor `cdp_permits.py` to use the helper**

Replace lines 26-33 (the `_haversine_ft` helper) and the matching loop at lines 72-86 of `sources/cdp_permits.py`. The new file should import:

```python
from pipeline.spatial import match_records_to_parcels
```

and remove the `from math import ...` and `_haversine_ft` definition. Replace lines 72-86 (the latest dict construction with the inner haversine loop) with:

```python
    matches = match_records_to_parcels(raw_rows, parcels, MATCH_RADIUS_FT)
    latest: dict[str, str] = {}
    for idx, pin in matches.items():
        r = raw_rows[idx]
        if not r["issue_date"]:
            continue
        if pin not in latest or r["issue_date"] > latest[pin]:
            latest[pin] = r["issue_date"]
```

`parcels` rows from sqlite have the keys `pin`, `lat`, `lng` already, matching the helper's expected fields.

- [ ] **Step 6: Run permit tests**

Run: `pytest tests/test_source_cdp_permits.py -v`

Expected: all PASS.

- [ ] **Step 7: Refactor `cdp_violations.py` similarly**

Replace `_haversine_ft` import/def in `sources/cdp_violations.py` with `from pipeline.spatial import match_records_to_parcels`. Replace the matching loop (lines 72-91, the `for r in raw_rows: ... best_pin, best_d = ...`) with:

```python
    open_records = [
        (i, r) for i, r in enumerate(raw_rows)
        if (r.get("violation_status") or "").upper().startswith("OPEN")
    ]
    open_indices = {i for i, _ in open_records}
    matches = match_records_to_parcels(raw_rows, parcels, MATCH_RADIUS_FT)
    open_count: dict[str, int] = defaultdict(int)
    oldest_open: dict[str, str] = {}
    for idx, pin in matches.items():
        if idx not in open_indices:
            continue
        r = raw_rows[idx]
        open_count[pin] += 1
        vd = r["violation_date"]
        if vd and (pin not in oldest_open or vd < oldest_open[pin]):
            oldest_open[pin] = vd
```

- [ ] **Step 8: Run violations tests**

Run: `pytest tests/test_source_cdp_violations_vacant.py -v`

Expected: all PASS.

- [ ] **Step 9: Refactor `cdp_vacant.py` similarly**

Replace `_haversine_ft` import/def and the matching loop at lines 75-85 of `sources/cdp_vacant.py` with:

```python
    matches = match_records_to_parcels(raw_rows, parcels, MATCH_RADIUS_FT)
    flagged: set[str] = set(matches.values())
```

- [ ] **Step 10: Run vacant tests**

Run: `pytest tests/test_source_cdp_violations_vacant.py -v` (covers both)

Expected: all PASS.

- [ ] **Step 11: Run full suite**

Run: `pytest -q`

Expected: 96 passing (+4 spatial tests).

- [ ] **Step 12: Commit**

```bash
git add pipeline/spatial.py sources/cdp_permits.py sources/cdp_violations.py sources/cdp_vacant.py tests/test_pipeline_spatial.py
git commit -m "perf: vectorize permit/violation/vacant spatial match with sjoin_nearest

The pure-Python O(parcels × records) haversine inner loops would not complete
at full geography (~8 days for cdp_violations alone). Replaced with a shared
geopandas.sjoin_nearest against EPSG:3435 (Illinois East feet), keeping the
50ft radius semantics exact while running in seconds at full scale."
```

---

### Task 8: Add indexes for default sort and high-cardinality filters

`pipeline/db.py:78-81` declares only four indexes; the default ORDER BY is `last_updated_date DESC, hold_duration_years DESC` (`webapp/parcel_query.py:18-20`). Neither is indexed. Filter combinations on `is_absentee`, `is_llc`, `tax_delinquent`, `consolidation_group_id` also full-scan today.

**Files:**
- Modify: `pipeline/db.py:78-81`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_db.py` (or create it if it doesn't exist — verify with `ls tests/test_db.py` first):

```python
from pipeline.db import init_db, get_connection


def test_init_db_creates_filter_and_sort_indexes(tmp_path):
    """Indexes for filter/sort columns must exist after init_db so the UI's
    default queries don't full-scan at full-geography scale."""
    db = tmp_path / "ix.db"
    init_db(db)
    conn = get_connection(db)
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='parcels'"
    ).fetchall()
    names = {r[0] for r in rows}
    expected = {
        "idx_parcels_zone_class",
        "idx_parcels_property_class",
        "idx_parcels_score",
        "idx_parcels_stage",
        "idx_parcels_last_updated_date",
        "idx_parcels_hold_duration_years",
        "idx_parcels_is_absentee",
        "idx_parcels_is_llc",
        "idx_parcels_tax_delinquent",
        "idx_parcels_consolidation_group_id",
    }
    missing = expected - names
    assert not missing, f"Missing indexes: {missing}"
```

- [ ] **Step 2: Run the test**

Run: `pytest tests/test_db.py::test_init_db_creates_filter_and_sort_indexes -v`

Expected: FAIL — six new indexes are missing.

- [ ] **Step 3: Add the indexes**

Edit `pipeline/db.py` to extend the index block right after line 81. After:

```sql
CREATE INDEX IF NOT EXISTS idx_parcels_stage ON parcels(stage);
```

add:

```sql
CREATE INDEX IF NOT EXISTS idx_parcels_last_updated_date ON parcels(last_updated_date);
CREATE INDEX IF NOT EXISTS idx_parcels_hold_duration_years ON parcels(hold_duration_years);
CREATE INDEX IF NOT EXISTS idx_parcels_is_absentee ON parcels(is_absentee);
CREATE INDEX IF NOT EXISTS idx_parcels_is_llc ON parcels(is_llc);
CREATE INDEX IF NOT EXISTS idx_parcels_tax_delinquent ON parcels(tax_delinquent);
CREATE INDEX IF NOT EXISTS idx_parcels_consolidation_group_id ON parcels(consolidation_group_id);
```

- [ ] **Step 4: Run the test**

Run: `pytest tests/test_db.py::test_init_db_creates_filter_and_sort_indexes -v`

Expected: PASS.

- [ ] **Step 5: Run full suite**

Run: `pytest -q`

Expected: 97 passing.

- [ ] **Step 6: Commit**

```bash
git add pipeline/db.py tests/test_db.py
git commit -m "perf: add indexes for default sort + high-cardinality filter columns

The webapp default ORDER BY (last_updated_date, hold_duration_years) and the
common filter columns (is_absentee, is_llc, tax_delinquent, consolidation_group_id)
were full-scanning. Added six indexes pre-load so the index build amortizes
during ingestion rather than blocking the first UI page-load at full scale."
```

---

### Task 9: Add explicit `$order` to paginated Socrata fetches

`pipeline/socrata.py:39-68` uses offset pagination with no `$order` set unless callers pass one. Without an explicit ordering, Socrata is allowed to return rows in any order across pages — meaning offset paging can miss or duplicate rows when the dataset is mutated mid-fetch. At >50K-row datasets this becomes a real correctness concern.

Fix: when no `order` is supplied, default to `:id ASC` (the SODA universal record id, supported on every Socrata dataset). Caller-supplied orders are unchanged.

**Files:**
- Modify: `pipeline/socrata.py:39-58`
- Test: `tests/test_socrata.py` (extend existing)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_socrata.py`:

```python
@responses.activate
def test_fetch_defaults_to_id_order_when_none_passed():
    """Without an explicit $order, offset pagination is unsafe across multiple
    pages because Socrata may return rows in arbitrary order. Default to :id."""
    captured = {}

    def callback(request):
        captured["params"] = dict(request.params)
        return (200, {}, "[]")

    responses.add_callback(
        responses.GET,
        "https://data.cityofchicago.org/resource/abcd-1234.json",
        callback=callback,
    )
    client = SocrataClient(domain="data.cityofchicago.org", retry_backoff=0.0)
    list(client.fetch("abcd-1234"))
    assert captured["params"].get("$order") == ":id"


@responses.activate
def test_fetch_preserves_caller_supplied_order():
    """Explicit $order from the caller must not be clobbered by the default."""
    captured = {}

    def callback(request):
        captured["params"] = dict(request.params)
        return (200, {}, "[]")

    responses.add_callback(
        responses.GET,
        "https://data.cityofchicago.org/resource/abcd-1234.json",
        callback=callback,
    )
    client = SocrataClient(domain="data.cityofchicago.org", retry_backoff=0.0)
    list(client.fetch("abcd-1234", order="year DESC"))
    assert captured["params"].get("$order") == "year DESC"
```

If `tests/test_socrata.py` doesn't already import `responses` and `SocrataClient`, look at the existing imports there and match the style.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_socrata.py -v -k "default_to_id_order or preserves_caller"`

Expected: FAIL on the default test (no $order is set today), PASS on the preserves test (current code already preserves it).

- [ ] **Step 3: Apply the default**

Edit `pipeline/socrata.py:39-66`. Inside `fetch`, change the `if order:` block to:

```python
            params["$order"] = order if order else ":id"
```

Replacing the original:

```python
            if order:
                params["$order"] = order
```

- [ ] **Step 4: Run the tests**

Run: `pytest tests/test_socrata.py -v`

Expected: all PASS.

- [ ] **Step 5: Run full suite**

Run: `pytest -q`

Expected: 99 passing (+2 socrata tests).

- [ ] **Step 6: Commit**

```bash
git add pipeline/socrata.py tests/test_socrata.py
git commit -m "fix: default $order=:id on Socrata fetches without explicit order

Offset pagination across >50K-row datasets is only safe when paired with an
explicit ordering — without it, Socrata may return rows in arbitrary order
across pages and offset paging can miss or duplicate records. :id is the
universal SODA record id and is available on every dataset."
```

---

## Final Verification

- [ ] **Step 1: Re-run the smoke fetch end-to-end with all fixes in place**

Run: `python -m pipeline.fetch_all --config-dir config_smoke --db data/smoke_v3.db`

Expected: every source `ok`, cdp_zoning > 0 rows, total run time noticeably faster than the pre-Phase-2 baseline (the spatial-match rewrite is the dominant speedup).

- [ ] **Step 2: Confirm column-population metrics**

Run:

```bash
sqlite3 data/smoke_v3.db <<'SQL'
SELECT
  ROUND(100.0 * SUM(assessed_total IS NOT NULL) / COUNT(*), 1) AS pct_assessed_total,
  ROUND(100.0 * SUM(estimated_annual_tax IS NOT NULL) / COUNT(*), 1) AS pct_est_tax,
  ROUND(100.0 * SUM(zone_class IS NOT NULL) / COUNT(*), 1) AS pct_zone_class,
  ROUND(100.0 * SUM(is_absentee = 1) / COUNT(*), 1) AS pct_absentee,
  ROUND(100.0 * SUM(is_llc = 1) / COUNT(*), 1) AS pct_llc,
  COUNT(*) AS total
FROM parcels;
SQL
```

Expected: assessed_total > 80%, estimated_annual_tax > 80%, zone_class > 90%, absentee dropped from 89% to ~30-60%, llc up modestly.

- [ ] **Step 3: Run full pytest one final time**

Run: `pytest -q`

Expected: 99 passing, 0 failing.

- [ ] **Step 4: Tag the milestone**

```bash
git tag -a phase-1-2-pre-scale-ready -m "Pre-scale fixes complete — ready for full-geography fetch"
```
