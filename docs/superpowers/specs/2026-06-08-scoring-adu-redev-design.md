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
2. **Profile-level filter presets** — picking a profile in the UI auto-applies a set of recommended filters (e.g., picking "ADU" applies `adu_eligible=1`, `lot_size_sf BETWEEN 3500-12000`, `is_condo_unit=0`). Filters live in the existing UI/`filter_schema` layer, NOT in the scoring YAML — operators can toggle them off without re-running the engine.
3. **New per-parcel attributes** — lot width (derived from polygons we already fetch) and ADU eligibility (derived from a new ArcGIS data source).

**Separation of concerns:**

| Layer | Purpose | Source of truth |
|---|---|---|
| Scoring YAML | "Given a parcel, how well does it match this strategy?" — pure signals + weights | `config/scoring_<profile>.yaml` |
| Profile filter defaults | "When the operator picks this profile, which filters auto-apply?" | `config/profile_defaults.yaml` |
| UI filters | Live filter state — fully operator-controlled, URL-shareable | Existing `filter_schema.py` + `/api/filters` |
| Profile dropdown | Picks the `score_*` column that ranks the filtered set + triggers default-filter auto-apply | New UI component |

Every parcel gets every profile score. The scoring engine doesn't know about filters at all. Operators narrow the pool via UI filters; the profile dropdown ranks within the pool. A non-ADU-eligible parcel still has a `score_adu` value (typically low, because `adu_eligible` is a heavily-weighted negative for non-eligible parcels) — it just won't appear in the default ADU view because the auto-applied `adu_eligible=1` filter excludes it. The operator can toggle that filter off to see how it would have ranked.

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
ALTER TABLE parcels ADD COLUMN score_value_add REAL;             -- replaces legacy 'score'
ALTER TABLE parcels ADD COLUMN score_adu REAL;
ALTER TABLE parcels ADD COLUMN score_redev REAL;
-- one-time backfill in the migration:
UPDATE parcels SET score_value_add = score WHERE score IS NOT NULL;
```

The legacy `score` column stays for one release as a back-compat shim (kept in sync with `score_value_add` via a trigger) so external queries don't break mid-migration. Removed in a follow-up cleanup PR after a sanity-check pass.

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

### 4. Scoring engine extension: one mechanism, every profile gets a column

This subsumes and replaces the prior `--scoring-yaml` swap mechanism documented in `config/scoring_alternatives/lot_size_sf_positive.yaml`'s header. Going forward, **every scoring YAML gets its own profile column**, and the engine runs all registered profiles in one pass.

**YAML schema** (one new top-level field — `profile_name`):
```yaml
version: string
profile_name: string             # determines the score_<profile_name> column to write
top_n: integer
signals:
  <signal_name>:
    weight: float
    kind: "continuous" | "binary"
    direction: "positive" | "negative"
    normalization: {min: float, max: float}   # required for continuous
```

**Engine signature change:** `score_parcels(db_path, scoring_config)` becomes `score_parcels(db_path, scoring_configs: list[ScoringConfig])`. Each config writes to `score_<profile_name>`. The canonical `config/scoring.yaml` keeps `profile_name: value_add` and writes to a `score_value_add` column (NOT the legacy `score` column — see migration note below).

**Always-run profile registry** lives in `config/profile_defaults.yaml` (the same file as the per-profile recommended_filters — keeps everything profile-related in one place):
```yaml
profiles:                                 # ordered; first is the UI default
  - name: value_add
    yaml: config/scoring.yaml
  - name: adu
    yaml: config/scoring_adu.yaml
  - name: redev
    yaml: config/scoring_redev.yaml

defaults:                                 # per-profile recommended filters (unchanged from §4b)
  value_add:
    recommended_filters: {}
  adu:
    recommended_filters: ...
  redev:
    recommended_filters: ...
```

**`--scoring-yaml` flag preserved for one-off experiments.** It accepts a `--profile-name <name>` companion that determines the column name to write (defaults to the YAML's own `profile_name` field if not given). Use case: `python -m pipeline.score --scoring-yaml my-experiment.yaml --profile-name tmp` writes `score_tmp` for ad-hoc comparison. Drop the column when done. No registration in `profile_defaults.yaml` required for experiments.

**Migration of the legacy `score` column.** The existing `score` column (currently written by `config/scoring.yaml`) is renamed conceptually to `score_value_add` via a one-time data migration:
```sql
ALTER TABLE parcels ADD COLUMN score_value_add REAL;
UPDATE parcels SET score_value_add = score;
-- the legacy `score` column stays for one release as a back-compat shim,
-- mirroring score_value_add via a trigger; removed in a follow-up cleanup PR
```
This keeps any external query that reads `score` working during the transition without a code freeze. The UI dropdown immediately uses `score_value_add`.

No filter parsing. No SQL WHERE clause appended. Every parcel gets every profile's score. Parcels that don't fit a strategy score low on its profile — that's the signal, not exclusion.

### 4b. Profile filter defaults

Defined in the same `config/profile_defaults.yaml` as the always-run registry (full example shown in §4 above). Each registered profile gets a `recommended_filters` block:

```yaml
defaults:
  value_add:
    recommended_filters: {}            # default: no auto-applied filters

  adu:
    recommended_filters:
      adu_eligible: 1
      is_condo_unit: 0
      lot_size_sf: {between: [3500, 12000]}
      lot_width_ft: {not_null: true}

  redev:
    recommended_filters:
      is_condo_unit: 0
      lot_size_sf: {min: 5000}
      zone_class: {prefix_in: ["RT-", "RM-", "B", "C"]}
```

The webapp loads this at startup and exposes the per-profile defaults via the parcels list route. When the operator picks a profile in the dropdown, the frontend merges these defaults into the URL filter state. Operators can toggle any of them off afterward — the URL is the source of truth, defaults are just the initial values.

**Filter operator shape** matches existing `/api/filters` semantics: equality is implicit, range / list operators are nested objects. `filter_schema.py` is extended to register `adu_eligible`, `adu_has_annual_limits`, `adu_restriction_text`, and `lot_width_ft` as filterable columns — purely additive.

### 5. Profile YAMLs

#### `config/scoring_adu.yaml`

```yaml
version: 1.0.0-2026-06-08
top_n: 20
signals:
  lot_width_ft:
    weight: 0.30
    kind: continuous
    direction: positive
    normalization: {min: 25, max: 50}        # 25 ft = standard Chicago lot, 50 ft = wide
  lot_size_sf:
    weight: 0.15
    kind: continuous
    direction: positive
    normalization: {min: 3500, max: 12000}
  adu_eligible:                                # heavy positive — non-eligible parcels sink
    weight: 0.20
    kind: binary
    direction: positive
  adu_has_annual_limits:                       # derived binary from adu_restriction_text
    weight: 0.05
    kind: binary
    direction: negative                        # annual cap = slower permitting
  last_sale_price_recent:                      # populated only when hold_duration_years ≤ 3
    weight: 0.10
    kind: continuous
    direction: negative
    normalization: {min: 200000, max: 1500000}
  hold_duration_years:
    weight: 0.10
    kind: continuous
    direction: positive                        # long-held = motivated seller proxy
    normalization: {min: 5, max: 30}
  years_since_last_permit:
    weight: 0.10
    kind: continuous
    direction: positive                        # deferred maintenance proxy
    normalization: {min: 3, max: 25}
```

Weights sum to 1.00. Auto-applied filters (in `profile_defaults.yaml`): `adu_eligible=1`, `is_condo_unit=0`, `lot_size_sf BETWEEN 3500-12000`, `lot_width_ft NOT NULL`.

**Pool sizing estimate:** ~3,000–5,000 parcels in the default ADU view (after auto-applied filters). Operators can toggle filters off to explore wider — every parcel still has a `score_adu` value for comparison.

#### `config/scoring_redev.yaml`

```yaml
version: 1.0.0-2026-06-08
top_n: 20
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

Weights sum to 1.00. Auto-applied filters (in `profile_defaults.yaml`): `is_condo_unit=0`, `lot_size_sf >= 5000`, `zone_class` starts with `RT-` / `RM-` / `B` / `C`.

**Pool sizing estimate:** ~10,000–15,000 parcels in the default redev view. Operators can toggle filters off to explore wider — every parcel still has a `score_redev` value for comparison.

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

**Two effects when the operator picks a profile:**

1. **Sort column change.** The parcels list query swaps `ORDER BY score DESC` for `ORDER BY score_<profile> DESC`. Existing filter / map / search state is untouched.
2. **Auto-apply recommended filters.** The frontend reads the profile's `recommended_filters` from `/api/profile-defaults`, merges them into the current URL filter state, and updates the URL. Operators can toggle any of them off afterward — the URL filter state is the source of truth.

Profile selection persists in `localStorage` so reload keeps the operator's choice. Filter state continues to live in the URL (already the existing pattern) so a profile-with-filters view is shareable.

**Auto-apply semantics:** non-destructive. If the operator already has `lot_size_sf BETWEEN 4000-10000` set and picks "ADU" (whose default is `3500-12000`), the existing 4000-10000 wins — defaults only fill in unset filters. Avoids stomping operator intent.

---

## Testing

Per-phase TDD. Each phase blocks on green tests before merge.

### Multi-profile scoring engine
- A list of `(profile_name, scoring_config)` tuples runs in one pass; each writes to its own `score_<profile_name>` column. The legacy `score` column is preserved (value-add profile writes to it).
- Each profile run produces a non-NULL score for every parcel — no filter exclusion. A parcel that scores poorly on one profile still has a comparable value for ranking against others.
- Smoke test: ADU and redev profiles produce distinct distributions on the same parcel set (sanity: top-20 lists shouldn't be identical).

### Profile defaults loader
- `config/profile_defaults.yaml` loads cleanly; an unknown filter operator nested under a profile raises at load with a clear message naming the profile + column.
- `/api/profile-defaults` returns the per-profile recommended_filters dict.
- `filter_schema.py` registers the new filterable columns (`adu_eligible`, `adu_has_annual_limits`, `adu_restriction_text`, `lot_width_ft`); each works through the existing `/api/parcels` filter flow.

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
- Both new YAMLs load without raising; the engine produces non-empty `score_adu` and `score_redev` distributions across all parcels.
- Weights sum to 1.00 for each profile (loader asserts this).
- A condo parcel has a meaningful `score_adu` (likely low because `adu_eligible=0`), proving filters are not blocking score computation.

### UI dropdown
- Selecting a profile re-issues the API call with `?profile=`; the rendered list re-sorts by the new score column.
- Selecting a profile also auto-merges recommended_filters into the URL filter state; user-set filters are preserved (defaults only fill unset slots).
- The selection persists in localStorage across reloads; the URL filter state stays the source of truth for filters.
- Filters / search / map state survive the profile switch (no full page reload).

---

## Implementation phases

Six PRs, each independently mergeable. Each phase's PR description references this spec.

| Phase | Deliverable | Tests | Gate before next phase |
|---|---|---|---|
| 1 | Schema migration: add 9 new columns to `parcels` (including `score_value_add` for the legacy `score` migration); init_db idempotent | New columns persist after `pipeline init`; legacy `score` mirror via trigger | All existing tests green; `SELECT score, score_value_add FROM parcels` returns equal values |
| 2 | Lot width backfill: extend `sources/ccgis_parcels.py` + one-time backfill run | Polygon math + DB write | Hunter spot-checks `lot_width_ft` for 5 known parcels |
| 3 | ADU enrichment loader: new `sources/chicago_adu_zones.py` + spatial join + `adu_eligible` / `adu_restriction_text` derivation | Spatial join + derivation logic | Hunter spot-checks `adu_eligible` for an RS parcel inside vs outside a polygon |
| 4 | Scoring engine extension: per-profile column output + `profile_name` YAML field + `--profile-name` CLI companion. Two new YAML files (adu, redev). `config/scoring.yaml` gains `profile_name: value_add`. `profile_defaults.yaml` (always-run registry + recommended_filters). `filter_schema.py` registers new filterable columns. Back-compat trigger keeps legacy `score` mirroring `score_value_add`. | Multi-profile run writes one column per registered YAML; experimental `--scoring-yaml --profile-name tmp` writes `score_tmp`; profile defaults loader; new filter columns work via `/api/parcels`; legacy `score` mirror trigger | **Hunter explicitly approves the proposed signal weights before merging.** Until merged, `score_adu` / `score_redev` columns stay NULL. |
| 5 | UI profile dropdown + auto-apply recommended filters + route serves `/api/profile-defaults` | Frontend selection behavior + filter auto-merge | Hunter reviews UX |
| 6 | End-to-end data refresh: rerun fetch_all → analyze → score across all 3 profiles; present top-20 per profile for sanity check | Smoke run produces expected pool sizes (~3-5k ADU, ~10-15k redev) | Hunter confirms top-20 lists look right before any outreach is initiated against them |

---

## Risks and open questions

**1. Lot width approximation for irregular parcels.** `minimum_rotated_rectangle` over-states width on L-shaped, wedge, or corner lots by up to ~10%. The ~1,500–2,000 irregular parcels in the DB will have slightly inflated `lot_width_ft`. Acceptable because scoring cares about ordering, not exact dimensions. Mitigation: surface the polygon area : MBR area ratio as a "shape regularity" metric in a future iteration if this proves to mis-rank candidates.

**2. ADU map staleness.** The City publishes the polygon layer infrequently (months between updates). Our `adu_restriction_text` could lag the actual ordinance state. Mitigation: monthly refresh cron; the loader logs the fetch timestamp into `raw_chicago_adu_zones.fetched_at` so the data age is observable.

**3. Owner-occupancy assumption baked into eligibility.** This design treats RS-zoned parcels in "Owner Occupancy Limits" polygons as ADU-eligible because Hunter plans to owner-occupy. A future operator with a pure-investment thesis would need `derive_adu_eligible` to take an `assume_owner_occupied: bool` parameter and gate RS+owner-occ-restriction zones accordingly. Out of scope for v1.

**4. AVM (Zillow / RealEstateAPI / ATTOM) deferred.** Hunter asked about a true $1.5M affordability ceiling via AVM. This spec uses `last_sale_price_recent` (≤3 years old) as a proxy; missing/stale prices get neutral scoring (0.5) instead of penalized. A future spec adds AVM enrichment (~$0.10/parcel × 67k = ~$6,700 one-time, plus refresh cadence). Flagged as a separate ticket — does not block this work.

**5. "Annual Limits" zones for RS parcels.** Modeled as a 0.10-weighted negative binary signal (`adu_has_annual_limits`). If Hunter finds the annual cap rarely blocks deals in practice, drop the signal in a follow-up. If it blocks deals frequently, promote to a hard filter.

**6. Top-N tuning.** Set to 20 for both new profiles to match the existing value-add cap. If the ADU or redev list runs dry (operator works through 20 in a few weeks), bump per-profile in a config change — no code or schema work needed.

**7. Legacy `score` column migration.** The legacy `score` column (currently the only score) is moving to `score_value_add`. A trigger keeps `score` mirroring `score_value_add` for one release as a back-compat shim — external queries / scripts / notebooks that read `score` keep working during the transition. Cleanup PR drops the legacy column after a sanity check (~1 week after Phase 4 lands). Risk: anything that *writes* to `score` directly (none in the current codebase, but possible in untracked notebooks) would silently lose its write when the column is dropped. Mitigation: the trigger is one-way (`score_value_add` → `score`) so direct writes to `score` get clobbered by the trigger; document this clearly in the migration PR.

---

## Out of scope

- A "compare across profiles" view in the UI. The single dropdown ranks by one profile; cross-profile comparison stays a future feature (the data is there — every parcel has every profile's score column — but no UI surface for it yet).
- Outreach template variants per profile. Templates stay shared; profile only affects which parcels appear in the queue.
- Removal of the legacy `score` column. Phase 4 ships the back-compat trigger; the actual column drop is a follow-up cleanup PR after ~1 week of sanity-check.

---

## What gets committed before Phase 4

Through Phase 3, all the data plumbing lands but **no scoring weights are activated**. `score_adu` and `score_redev` columns exist but stay NULL. The two profile YAML files don't ship until Phase 4 — when Hunter explicitly approves the proposed numbers. This satisfies the standing instruction: "Don't change the scores without confirming first."
