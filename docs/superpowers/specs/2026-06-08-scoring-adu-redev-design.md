# Scoring Profiles: ADU + Redevelopment — Design

**Status:** Draft, 2026-06-08. Brainstormed and validated this session against real Cook County GIS data and the City of Chicago ADU Eligibility ArcGIS layer. No scoring weights are activated until Hunter signs off on the proposed numbers in Phase 4.

**Goal:** Surface two new acquisition strategies alongside the existing value-add multifamily score:

- **ADU profile** — extra-wide residential lots where the operator can plausibly add an Accessory Dwelling Unit. Constrained by lot size band (3,500–12,000 sf), lot width sweet spot, affordability ceiling, and the City's actual ADU-eligibility rules (zone-class based for RT/RM/B/C1/C2; polygon-lookup-based for RS-1/2/3).
- **Redevelopment profile** — underutilized parcels with high `far_gap_delta`, no price cap. Different optimum than ADU (bigger is better, no upper lot size, expensive is fine).

The existing `config/scoring.yaml` stays as the value-add multifamily profile — unchanged. New profiles add to the catalog rather than replacing.

**Phasing:** Six sequenced PRs, each with its own tests and merge gate. YAML weights stay dormant (no `score_adu` / `score_redev` columns populated in production) until Hunter explicitly approves them in Phase 4.

---

## Architecture

The system has three concerns that previously didn't exist:

1. **Multi-profile scoring** — one engine run produces multiple `score_*` columns from multiple profile YAMLs.
2. **Hard filters** — some signals are "in or out" (must be ADU-eligible, must not be a condo), not "more or less." The scoring engine needs to express filters distinct from weighted signals.
3. **New per-parcel attributes** — lot width (derived from polygons we already fetch) and ADU eligibility (derived from a new ArcGIS data source).

### 1. New `parcels` columns (additive schema migration)

```sql
-- Lot geometry from CCGIS polygons (populated in Phase 2)
ALTER TABLE parcels ADD COLUMN lot_width_ft REAL;
ALTER TABLE parcels ADD COLUMN lot_depth_ft REAL;

-- ADU eligibility from City ArcGIS layer + zone_class derivation (Phase 3)
ALTER TABLE parcels ADD COLUMN adu_eligible INTEGER;           -- 0 / 1 / NULL
ALTER TABLE parcels ADD COLUMN adu_restriction_text TEXT;      -- NULL for citywide-eligible,
                                                                -- string from polygon for RS-in-polygon
ALTER TABLE parcels ADD COLUMN adu_has_annual_limits INTEGER;  -- 1 if adu_restriction_text contains
                                                                -- 'Annual Limits', else 0 (Phase 3)

-- Derived in the Analyze step (Phase 4 dependency)
ALTER TABLE parcels ADD COLUMN last_sale_price_recent REAL;    -- = last_sale_price when
                                                                -- hold_duration_years <= 3, else NULL

-- Per-profile scores written by the scoring engine (Phase 4)
ALTER TABLE parcels ADD COLUMN score_adu REAL;
ALTER TABLE parcels ADD COLUMN score_redev REAL;
```

The existing `score` column stays as the value-add profile's output (renamed conceptually but not in SQL — too many queries reference it by name).

### 2. Lot width / depth backfill

`sources/ccgis_parcels.py` already fetches every parcel's polygon from Socrata dataset `77tz-riq7` and computes area in EPSG:3435 (US survey feet). It currently discards everything except `area_sf`. Extension:

```python
# Compute the oriented bounding box per polygon → true (width, depth) in feet.
mbr = gdf.geometry.minimum_rotated_rectangle
# minimum_rotated_rectangle returns a Polygon; extract the two side lengths
coords = mbr.exterior.coords[:5]  # 4 corners + closing point
side_a = Point(coords[0]).distance(Point(coords[1]))
side_b = Point(coords[1]).distance(Point(coords[2]))
width_ft = min(side_a, side_b)
depth_ft = max(side_a, side_b)
```

`raw_ccgis_parcels` table gains `width_ft` and `depth_ft` columns; the `UPDATE parcels SET lot_size_sf = ...` query also writes `lot_width_ft` and `lot_depth_ft` from the same per-pin10 row.

**One-time backfill:** re-running `python -m pipeline.fetch_all --source ccgis_parcels` over the existing geography takes ~30 minutes (Socrata pagination at 50k row limit + polygon math). No new API key required — same Socrata endpoint we already use.

### 3. ADU eligibility enrichment

New source: `sources/chicago_adu_zones.py`. Fetches polygons from the City's ArcGIS REST endpoint:

```
https://services7.arcgis.com/A03QrhyHnDaUmK0W/arcgis/rest/services/ADUAllowedRS2AA_view/FeatureServer/0
```

Each polygon carries:
- `ADU_Area` — numeric code identifying restrictions (e.g. `"1, 2"`, `"No Limitations"`)
- `Zone` — polygon ID (`"Zone 1"`, `"Zone 2"`, ...)
- `Text` — human-readable restriction description (`"Annual Limits, Owner Occupancy Limits"`, `"No Limitations"`, etc.)
- `Display_Te` — likely same as `Text` (sample showed identical values; loader stores `Text`)

**Spatial join** to every parcel's centroid (`parcels.lat`, `parcels.lng`). Parcels inside any polygon get `adu_restriction_text` set to that polygon's `Text` field. Parcels outside all polygons get `adu_restriction_text = NULL`.

**`adu_eligible` derivation** (set in the same loader, post-spatial-join):

```python
def derive_adu_eligible(zone_class: str, in_rs_polygon: bool) -> int:
    z = (zone_class or "").upper()
    # Citywide ADU-eligible zones — no polygon lookup needed.
    if z.startswith(("RT-", "RM-", "B", "C1-", "C2-")):
        return 1
    # RS zones require the polygon containment check.
    if z in ("RS-1", "RS-2", "RS-3"):
        return 1 if in_rs_polygon else 0
    # Everything else (M-*, PD, C3+, etc.) — not ADU eligible.
    return 0
```

This pulls together the rule from the City's instructions:

> If the zoning is RT, RM, any B, C1 or C2 — you are eligible for an ADU!
> If the property is zoned RS1, RS2, or RS3 — ADUs are only allowed if it is within an ADU-Allowed RS Area.

Refresh cadence: monthly (the City publishes updates infrequently). Document in the source's header. Same fetch script can be re-run idempotently.

**Owner-occupancy assumption.** Hunter is acquiring with intent to live in either the main house or the ADU. This satisfies the "Owner Occupancy Limits" restriction text on roughly half the polygons. If a future operator plans pure-investment acquisition, the `adu_eligible` derivation needs a flag to exclude RS-with-owner-occ-restriction zones — flagged as an open question below.

### 4. Scoring engine extension: filters block

The current YAML schema only supports weighted signals (`positive` / `negative` direction, linear normalization). To express "ADU candidates must be non-condo, 3,500–12,000 sf, and ADU-eligible," we extend the schema:

```yaml
filters:
  zone_class_prefix_in: ["RS-", "RT-", "RM-", "B", "C"]   # OR of prefixes
  is_condo_unit: 0                                          # equality
  lot_size_sf_between: [3500, 12000]                        # inclusive range
  lot_width_ft_not_null: true                               # presence check
  adu_eligible: 1                                           # equality

signals:
  # ... unchanged signal shape ...
```

Six supported operator suffixes (everything the two profiles need; resist adding more until a new profile requires it):

| Suffix | Meaning | SQL it compiles to |
|---|---|---|
| `_in` | column value in list | `col IN (...)` |
| `_prefix_in` | column starts with any of the listed prefixes | `(col LIKE 'a%' OR col LIKE 'b%' ...)` |
| `_between` | column value within `[min, max]` inclusive | `col BETWEEN ? AND ?` |
| `_min` / `_max` | one-sided range | `col >= ?` / `col <= ?` |
| `_not_null` | column IS NOT NULL | `col IS NOT NULL` |
| (no suffix) | equality (handles 0/1 booleans, strings, ints) | `col = ?` |

Parser lives in `pipeline/score.py`. Filters compile to a SQL `WHERE` clause that's appended to the parcel query — parcels excluded by filters get `score_<profile> = NULL` (NOT 0 — NULL means "not in this profile's pool" and the UI excludes them).

**Engine signature change:** `score_parcels(db_path, scoring_config)` becomes `score_parcels(db_path, scoring_configs: list[(profile_name, scoring_config)])`. Each profile's run writes to its own `score_<profile_name>` column. The current `score` column is preserved (the value-add profile writes to it).

### 5. Profile YAMLs

#### `config/scoring_adu.yaml`

```yaml
version: 1.0.0-2026-06-08
top_n: 20

filters:
  is_condo_unit: 0
  lot_size_sf_between: [3500, 12000]
  lot_width_ft_not_null: true
  adu_eligible: 1

signals:
  lot_width_ft:
    weight: 0.35
    kind: continuous
    direction: positive
    normalization: {min: 25, max: 50}        # 25 ft = standard Chicago lot, 50 ft = wide
  lot_size_sf:
    weight: 0.15
    kind: continuous
    direction: positive
    normalization: {min: 3500, max: 12000}
  adu_has_annual_limits:                       # derived binary from adu_restriction_text
    weight: 0.10
    kind: binary
    direction: negative                        # annual cap = slower permitting
  last_sale_price_recent:                      # populated only when hold_duration_years ≤ 3
    weight: 0.15
    kind: continuous
    direction: negative
    normalization: {min: 200000, max: 1500000}
  hold_duration_years:
    weight: 0.15
    kind: continuous
    direction: positive                        # long-held = motivated seller proxy
    normalization: {min: 5, max: 30}
  years_since_last_permit:
    weight: 0.10
    kind: continuous
    direction: positive                        # deferred maintenance proxy
    normalization: {min: 3, max: 25}
```

**Pool sizing estimate:** ~3,000–5,000 ADU-eligible parcels after filters. Tighter than the value-add pool because of the lot size band + ADU eligibility constraints.

#### `config/scoring_redev.yaml`

```yaml
version: 1.0.0-2026-06-08
top_n: 20

filters:
  is_condo_unit: 0
  lot_size_sf_min: 5000                        # too small to bother redeveloping
  zone_class_prefix_in: ["RT-", "RM-", "B", "C"]   # multifamily-allowing or commercial

signals:
  far_gap_delta:
    weight: 0.30                               # headline 'underzoned' signal
    kind: continuous
    direction: positive
    normalization: {min: 0.5, max: 2.5}
  is_low_util_land:
    weight: 0.20
    kind: binary
    direction: positive
  lot_size_sf:
    weight: 0.15
    kind: continuous
    direction: positive
    normalization: {min: 5000, max: 50000}
  max_far:
    weight: 0.10
    kind: continuous
    direction: positive
    normalization: {min: 1.0, max: 7.0}
  allows_multifamily_by_right:
    weight: 0.10
    kind: binary
    direction: positive                        # POSITIVE here — opposite of value-add profile
  cta_distance_ft:
    weight: 0.10
    kind: continuous
    direction: negative                        # closer to CTA = better
    normalization: {min: 500, max: 4000}
  hold_duration_years:
    weight: 0.05
    kind: continuous
    direction: positive
    normalization: {min: 5, max: 30}
```

**Pool sizing estimate:** ~10,000–15,000 redev candidates. Larger than ADU because no FAR delta hard floor and no upper lot size cap.

#### Two derived columns this design depends on

- `last_sale_price_recent` — a view of `last_sale_price` populated only when `hold_duration_years ≤ 3`; NULL otherwise. Derived in the Analyze step (where signal normalization happens today) before the scoring engine reads it.
- `adu_has_annual_limits` — binary; `1` if `adu_restriction_text` LIKE `'%Annual Limits%'`, else `0`. Derived in the ADU enrichment loader.

Both are pure projections of existing data — no new external lookups. They live as actual columns rather than YAML-time computations so the scoring engine's signal model stays simple.

### 6. UI changes

The current top bar shows score version + "Top N shown" metadata. Add a profile dropdown next to it:

```html
<select id="profile-selector">
  <option value="value_add">Value-add multifamily (default)</option>
  <option value="adu">ADU candidates</option>
  <option value="redev">Redevelopment</option>
</select>
```

Selecting a profile re-issues the parcels list query with `?profile=adu`; the route's existing `_build_where` adds `WHERE score_adu IS NOT NULL ORDER BY score_adu DESC LIMIT 20`. Filters/map/search continue working — they're orthogonal to which score column ranks the list.

Profile selection persists in `localStorage` so reload keeps the operator's choice.

---

## Testing

Per-phase TDD. Each phase blocks on green tests before merge.

### Filter block parsing + application
- YAML with each operator type loads without error; an unknown operator suffix raises with a clear message naming the column.
- Filter SQL compilation: `lot_size_sf_between: [3500, 12000]` → `lot_size_sf BETWEEN 3500 AND 12000`; combinable; null handling explicit.
- A parcel that matches all filters gets a non-NULL `score_<profile>`; a parcel that fails any filter gets NULL.
- Two profiles run in one engine pass produce two distinct `score_*` columns with independent pools.

### Lot width backfill
- `minimum_rotated_rectangle` on a 25 ft × 125 ft fixture polygon returns `(25.0 ± 0.5, 125.0 ± 0.5)`.
- An L-shaped fixture polygon doesn't crash (returns the bounding-box approximation; acceptable per the risk callout).
- After backfill, `parcels.lot_width_ft` populated for all rows with a polygon match in `raw_ccgis_parcels`; NULL elsewhere.

### ADU eligibility enrichment
- Spatial join: a parcel inside a fixture polygon gets `adu_restriction_text` set; one outside gets NULL.
- `derive_adu_eligible` returns 1 for all `("RT-3", True/False)`, `("RM-5", True/False)`, `("B3-2", True/False)`, `("C1-2", True/False)`, `("C2-3", True/False)`.
- Returns 1 for `("RS-3", True)`, 0 for `("RS-3", False)`.
- Returns 0 for `("M1-2", True/False)`, `("PD 853", True/False)`, `("C3-2", True/False)`, `(None, True/False)`.

### Profile YAMLs
- Both new YAMLs load without raising; the engine produces non-empty `score_adu` and `score_redev` distributions on the smoke DB.
- A parcel that fails the ADU `is_condo_unit: 0` filter has `score_adu IS NULL` and a non-NULL `score` (value-add doesn't filter on this).

### UI dropdown
- Selecting a profile re-issues the API call with `?profile=`; the rendered list updates to the new ranking.
- The selection persists in localStorage across reloads.
- Filters / search / map state survive the profile switch (no full page reload).

---

## Implementation phases

Six PRs, each independently mergeable. Each phase's PR description references this spec.

| Phase | Deliverable | Tests | Gate before next phase |
|---|---|---|---|
| 1 | Schema migration: add 8 new columns to `parcels`; init_db idempotent | New columns persist after `pipeline init` | All existing tests green |
| 2 | Lot width backfill: extend `sources/ccgis_parcels.py` + one-time backfill run | Polygon math + DB write | Hunter spot-checks `lot_width_ft` for 5 known parcels |
| 3 | ADU enrichment loader: new `sources/chicago_adu_zones.py` + spatial join + `adu_eligible` / `adu_restriction_text` derivation | Spatial join + derivation logic | Hunter spot-checks `adu_eligible` for an RS parcel inside vs outside a polygon |
| 4 | Scoring engine extension: filters block, multi-profile output, two new YAML files | Filter parsing, multi-profile run | **Hunter explicitly approves the proposed signal weights before merging.** Until merged, `score_adu` / `score_redev` columns stay NULL. |
| 5 | UI profile dropdown + route changes | Frontend selection behavior | Hunter reviews UX |
| 6 | End-to-end data refresh: rerun fetch_all → analyze → score across all 3 profiles; present top-20 per profile for sanity check | Smoke run produces expected pool sizes (~3-5k ADU, ~10-15k redev) | Hunter confirms top-20 lists look right before any outreach is initiated against them |

---

## Risks and open questions

**1. Lot width approximation for irregular parcels.** `minimum_rotated_rectangle` over-states width on L-shaped, wedge, or corner lots by up to ~10%. The ~1,500–2,000 irregular parcels in the DB will have slightly inflated `lot_width_ft`. Acceptable because scoring cares about ordering, not exact dimensions. Mitigation: surface the polygon area : MBR area ratio as a "shape regularity" metric in a future iteration if this proves to mis-rank candidates.

**2. ADU map staleness.** The City publishes the polygon layer infrequently (months between updates). Our `adu_restriction_text` could lag the actual ordinance state. Mitigation: monthly refresh cron; the loader logs the fetch timestamp into `raw_chicago_adu_zones.fetched_at` so the data age is observable.

**3. Owner-occupancy assumption baked into eligibility.** This design treats RS-zoned parcels in "Owner Occupancy Limits" polygons as ADU-eligible because Hunter plans to owner-occupy. A future operator with a pure-investment thesis would need `derive_adu_eligible` to take an `assume_owner_occupied: bool` parameter and gate RS+owner-occ-restriction zones accordingly. Out of scope for v1.

**4. AVM (Zillow / RealEstateAPI / ATTOM) deferred.** Hunter asked about a true $1.5M affordability ceiling via AVM. This spec uses `last_sale_price_recent` (≤3 years old) as a proxy; missing/stale prices get neutral scoring (0.5) instead of penalized. A future spec adds AVM enrichment (~$0.10/parcel × 67k = ~$6,700 one-time, plus refresh cadence). Flagged as a separate ticket — does not block this work.

**5. "Annual Limits" zones for RS parcels.** Modeled as a 0.10-weighted negative binary signal (`adu_has_annual_limits`). If Hunter finds the annual cap rarely blocks deals in practice, drop the signal in a follow-up. If it blocks deals frequently, promote to a hard filter.

**6. Top-N tuning.** Set to 20 for both new profiles to match the existing value-add cap. If the ADU or redev list runs dry (operator works through 20 in a few weeks), bump per-profile in a config change — no code or schema work needed.

---

## Out of scope

- Bulk scoring CLI flags (per-profile invocation, etc.) — engine runs all profiles every refresh, no CLI surface needed.
- A "compare across profiles" view in the UI. The single dropdown ranks by one profile; cross-profile comparison stays a future feature.
- Outreach template variants per profile. Templates stay shared; profile only affects which parcels appear in the queue.
- Migration of historical `score` values to `score_value_add`. The existing column name persists; documentation notes the equivalence.

---

## What gets committed before Phase 4

Through Phase 3, all the data plumbing lands but **no scoring weights are activated**. `score_adu` and `score_redev` columns exist but stay NULL. The two profile YAML files don't ship until Phase 4 — when Hunter explicitly approves the proposed numbers. This satisfies the standing instruction: "Don't change the scores without confirming first."
