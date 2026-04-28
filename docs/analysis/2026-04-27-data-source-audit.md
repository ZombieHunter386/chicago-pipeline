# Data-Source Audit — Pre-Scoring

**Date:** 2026-04-27
**DB analyzed:** `data/full.db` (67,677 parcels, full target geography)
**Target use case:** find absentee owners holding underbuilt parcels in Lincoln Park / Lakeview
**Scope:** survey only — no code changes, no data pulls

---

## Stop the line

Three things to fix before scoring goes live.

1. **`tax_delinquent` is 100 % NULL.** `data/delinquent.csv` is a one-line, header-only stub (`pin,tax_year,amount_owed`). The pipeline ran clerk_delinquent OK but processed 0 rows. The spec assumed a free monthly bulk CSV from the Clerk; that bulk download does not actually exist publicly — see [Tier 1 §1](#tier-1-do-before-scoring) for access-pattern options. Right now this is the single highest-value motivation signal and we have zero data on it. **Don't run the historical analysis script with this column in the feature set — it'll learn that delinquency is uncorrelated with development.** Decide on access path (targeted scrape vs. FOIA/data purchase) before building the fetch module.

2. **Permit and violation match rates are surprisingly low. — FIXED in this branch (revised approach).**
   - `years_since_last_permit` was populated for **2,121 parcels (3.1 %)** even though we had 51,107 permits in the bbox.
   - `open_violations_count > 0` fired on **930 parcels (1.4 %)** even though there were 30,320 OPEN violations in the bbox across 6,032 distinct property-groups.

   Original prescription was a radius bump (50 ft → 200 ft). Replaced with an **address-first matcher** in `pipeline/spatial.match_records_to_parcels_with_address`, used by all four address-based CDP sources. Order: (1) exact normalized street-key match, (2) range expansion ("100-104 W DIVERSEY" → 100/102/104), (3) geo nearest within **75 ft**, (4) fuzzy match (same number+direction, Levenshtein ≤ 2 on street-name tokens) — **logged for review, not auto-matched.** This eliminates the false-positive class that a 200-ft radius would create (a violation halfway down the block attaching to the wrong parcel). `cdp_permits.py` and `cdp_violations.py` now use the shared matcher; their MATCH_RADIUS constants are gone. Run those fetches to re-match against the existing raw tables — no re-pull from Socrata needed.

3. **Mistyped zone_class strings — FIXED in this branch.** The raw zoning dataset has 13 polygons with no-hyphen residential codes (`RM5.5` × 5, `RM4.5` × 8) that fail to match the lookup, plus PD/PMD codes which are intentionally NULL because we don't have per-PD ordinances. Added a normalizer at [sources/cdp_zoning.py](../../sources/cdp_zoning.py) (`_normalize_zone_class`) that hyphenates `RS|RT|RM` + digit. Re-run the cdp_zoning fetch to re-apply the spatial join with the normalizer in place; PD/PMD remain NULL as before (separate work to populate per-PD).

Everything below assumes #1 has an access path picked and #2/#3 fixes have been re-run on the live DB.

---

## Section A — Current-state inventory

Population rates are computed against all 67,677 parcels in `data/full.db`. Where a column only makes sense for a subset (e.g. building data on condo units), the filtered rate is given separately. Reproduction SQL is at the end of the doc.

### Identity & geography (free of issues)

| Column | Source | Pop. rate | Notes |
|---|---|---|---|
| `pin`, `address`, `lat`/`lng` | Assessor 1A + 1B | 100 % / 100 % / 100 % | Clean. |
| `ward_num` | Assessor 1A | 94.1 % | A handful of bbox parcels lack ward; not a problem. |
| `zip_code` | Assessor 1A | 99.2 % | |
| `zone_class` | CDP 2A spatial join | 100 % | One value (`RM5.5`) doesn't match the lookup — see Stop the line §3. |
| `cta_distance_ft` | CDP 2F + Python haversine | 100 % | |
| `tif_district` | Assessor 1A | 34.4 % | True rate of TIF coverage in the bbox (33 % of bbox is in TIF — CHICAGO — TRANSIT RPM1, the Red/Purple Modernization TIF). Not a population issue. |

### Owner / motivation (mostly fine)

| Column | Source | Pop. rate | Notes |
|---|---|---|---|
| `owner_name`, `mail_address` | Assessor 1B | 100 % / 97.6 % | |
| `is_absentee` | derived | 100 % set; **54.5 % true** | Probably high — likely false-positives from condo units mailed to property-management addresses. Worth spot-checking that condo units in the same building don't all read as "absentee". |
| `is_llc` | regex on `mail_address_name` | 100 % set; 14.2 % true | Reasonable. |
| `last_sale_date`, `last_sale_price`, `hold_duration_years`, `deed_type` | Assessor 1E | 72.3 % each | The 27.7 % gap is likely parcels with no recorded arm's-length sale since 1999 — long-held property, which is *itself* a positive signal. Don't impute. |
| `appeal_count > 0` | Assessor 1F | 79.7 % | This is suspiciously high — Cook County publishes appeals back to 1999, so most parcels show ≥1 historical appeal. As a signal "ever filed an appeal" is too noisy; "filed in last 3 years" or "≥3 appeals in 5 years" would be more meaningful. Reframe before weighting. |

### Building characteristics (the condo / commercial gap)

| Column | Source | Population on **all** parcels | Population on **non-condo-unit** parcels (the ones the UI shows) | Notes |
|---|---|---|---|---|
| `lot_size_sf` | CCGIS polygons (1H) → fallback to chars | 83.8 % | likely 100 % visible | |
| `building_sf` | Assessor 1C `char_bldg_sf` | **22.1 %** | **64.5 %** | The big gap is condos (47,028 unit parcels, 0 % coverage) and most 5xx commercial / 3xx commercial-multifamily classes (4–11 % coverage). On 4,392 rolled-up condo *building* reps, only 29.1 % have building_sf — confirmed. |
| `year_built` | Assessor 1C `char_yrblt` | 22.2 % | 65.0 % | Same root cause. |
| `condition` | Assessor 1C | 22.2 % | 65.0 % | |
| `building_classification` | Assessor 1C | 22.1 % | 64.5 % | |
| `built_far` | derived = `building_sf / lot_size_sf` | 19.7 % | 57.6 % | Bottlenecked by `building_sf`. |

**Key insight on building data:** the Cook County Improvement Characteristics dataset (`x54s-btds`) covers single & multi-family only. It does *not* cover condo buildings, commercial mixed-use (3xx), commercial (5xx), or vacant land. Of the 23,130 visible parcels, the ~6,500 with no building data are mostly condo buildings (4,392 reps), commercial 5xx (~2,500 in bbox), commercial 3xx (~1,000), and 100/190 vacant land. Two of those four — condo buildings and 5xx/3xx commercial — are exactly the parcel types you most want to evaluate as redevelopment candidates. **This is the largest data gap in the pipeline.** See Tier 1 §3 (Chicago Building Footprints).

### Values / taxes

| Column | Source | Pop. rate | Notes |
|---|---|---|---|
| `assessed_total`, `assessed_land`, `assessed_building` | Assessor 1D | 100 % | |
| `land_building_ratio` | derived | 98.4 % | |
| `estimated_annual_tax` | derived from `tax_constants.yaml` | 98.5 % | Uses a single citywide composite rate (6.717 %). Per-tax-code rates would be materially better — see Tier 1 §2. |
| `tax_increase_pct_1yr` | derived from 1D history | 95.7 % (94.5 % visible) | Fine. |
| `tax_increase_pct_5yr` | derived from 1D history | 87.9 % (88.7 % visible) | Fine. |

### Distress signals (the broken ones)

| Column | Source | Pop. rate | Notes |
|---|---|---|---|
| **`tax_delinquent`** | Clerk 3A | **0.0 %** | **Showstopper — see Stop the line §1.** |
| `delinquency_years` | Clerk 3A | 0.0 % | Same. |
| **`open_violations_count > 0`** | CDP 2D | **1.4 %** | **Match rate too low — see Stop the line §2.** |
| `oldest_violation_age_days` | CDP 2D | 1.4 % | Same. |
| **`years_since_last_permit`** | CDP 2C | **3.1 %** | **Match rate too low — see Stop the line §2.** |
| `has_vacancy_report` | CDP 2E | 0.0 % | The 311 vacant-building dataset (`7nii-7srd`) is the legacy one — it ends in 2018. There's a current dataset `vauj-4grr` we should switch to. Even with the current dataset, LP/LV are not high-vacancy neighborhoods, so coverage will stay sparse. Lower priority than the others, but the legacy-dataset issue should be fixed regardless. |

### Zoning capacity

| Column | Source | Pop. rate (visible) | Notes |
|---|---|---|---|
| `max_far` | `zoning_lookup.yaml` | 91.1 % | The 8.9 % gap is **2,019 PD-zoned parcels** (no per-PD ordinance in the lookup) plus 46 POS, 1 mistyped `RM5.5`. Spec already flagged this. |
| `max_units_allowed` | `zoning_lookup.yaml` | 76.4 % | Same PD/POS gap, plus 392 M-x manufacturing parcels (no density limit defined in lookup). Manufacturing is ambiguous because zoning ordinances allow conversions; for now NULL is correct. |
| `min_lot_area_per_unit` | `zoning_lookup.yaml` | 90.3 % | |
| `allows_multifamily_by_right` | `zoning_lookup.yaml` | 91.1 % | |

### Raw-table coverage (sanity)

| Raw table | Rows | Distinct PINs / IDs |
|---|---|---|
| `raw_assessor_parcels` | 1,437,033 | 67,677 PINs (history rows × multiple years) |
| `raw_assessor_addresses` | 67,677 | 67,677 |
| `raw_assessor_characteristics` | 365,485 | (fewer PINs — history × cards) |
| `raw_assessor_values` | 1,475,258 | (history rows) |
| `raw_assessor_sales` | 121,794 | |
| `raw_assessor_appeals` | 150,367 | |
| `raw_assessor_exempt` | 910 | `exemption_type` is blank for all rows — fetch is dropping this column. Minor. |
| `raw_cdp_zoning` | 14,905 polygons | |
| `raw_cdp_permits` | 51,107 | |
| `raw_cdp_violations` | 60,770 | 6,032 distinct property_groups |
| `raw_cdp_vacant` | 384 | (148 fall in the LP/LV bbox) |
| `raw_cdp_cta_stations` | 145 | |
| `raw_ccgis_parcels` | 19,442 | (lot polygons) |
| **`raw_clerk_delinquent`** | **0** | **Stub.** |

---

## Section B — Gaps in current data, ranked by impact

In rough order of how much each gap would cost us if we calibrated scoring tomorrow:

1. **Tax delinquency is missing entirely.** The strongest motivation-to-sell signal in the literature, and we have nothing. (Stop the line §1.)
2. **Permit/violation matching is leaky.** ~95 % of permits and ~85 % of violations in the bbox aren't being attached to PINs. Both are core motivation signals. (Stop the line §2.)
3. **Building data is missing for condos and commercial.** ~6,500 of 23,130 visible parcels have no `building_sf` / `year_built`, including most condo buildings and 5xx/3xx commercial. Condo buildings and underbuilt commercial mixed-use are exactly the redevelopment targets we care most about. (Tier 1 §3.)
4. **Tax estimates use a single citywide rate.** `composite_rate_pct = 6.717` is applied uniformly. Per-tax-code rates from the Clerk would tighten estimates and let "rising tax burden" be a more honest signal. (Tier 1 §2.)
5. **Appeal-count signal is too coarse.** 80 % of parcels show ≥1 lifetime appeal — meaningless. Should be windowed (last 3 yrs, or "≥N appeals in 5 yrs"). Code-only fix.
6. **`is_absentee` is probably over-firing on condos.** 54.5 % true is high enough to suspect that condo units mailed to a management company read as absentee. Spot-check before weighting.
7. **PD-zoned parcels have NULL FAR/density.** 2,019 of them. Per-PD ordinance lookup would fix this; manual / one-time effort. Spec already calls this out.
8. **Vacancy reports use the legacy 311 dataset.** Switch `7nii-7srd` → `vauj-4grr`. Trivial.
9. **`raw_assessor_exempt.exemption_type` is blank.** Minor — we know which PINs are exempt, just not why. Easy fetch fix.
10. **No condo unit count per building.** `condo_unit_count` is in the schema but we don't have a source populating it (the rollup just counts constituent PINs, which is approximate). Would matter for scoring "condo deconversion" plays.

---

## Section C — New sources to add, prioritized

The categories the brief listed are addressed below, with verification of dataset IDs and access patterns. URLs cited are what I actually checked.

### Tier 1 — do before scoring

These either fix a column we already need, or unlock a high-signal column on a population we care about.

#### 1. Cook County Clerk — Delinquent Property Tax (replacement access path)

- **Status of current "free monthly bulk CSV" assumption: wrong.** I could not confirm any free, one-click bulk CSV/Excel of currently-delinquent PINs at https://www.cookcountyclerkil.gov/property-taxes/delinquent-property-tax-search. The portal is search-by-PIN only; the published "20-year delinquent property file field descriptions" PDF implies a paid bulk file purchasable from the Clerk's office. Public scraping of the search portal works but is slow and ToS-grey.
- **Practical options, ranked:**
  1. **Targeted scrape** of taxdelinquent.cookcountyclerkil.gov for our ~25k bbox PINs at ~1 req/3 s ≈ 21 hrs/run. Doable as a one-time fill, repeatable monthly. Same fragility class as the IL SOS scraper we already plan for.
  2. **FOIA / data-purchase request** to the Clerk's office for the bulk file. One-time effort, recurring cost unknown.
  3. **Treasurer-portal scrape** for the "Tax Sale" status (https://cookcountytreasurer.com) — different dataset, also useful.
- **What it unlocks:** the highest-value motivation signal in the design. Required for scoring.
- **Effort:** medium (new fetch module, plus pagination/throttling). 1–2 days.
- **Per-parcel:** yes, joins by PIN.

#### 2. Cook County Clerk — Tax Code Rates

- **Dataset:** `9sqg-vznj` ("Tax codes, agencies, and rates") on `datacatalog.cookcountyil.gov`. Live; returns `tax_year, tax_code, agency, agency_name, agency_rate, tax_code_rate`. The `tax_code_rate` is the per-tax-code composite. Free, Socrata, annual updates.
- **Companion field needed on parcels:** `tax_code` from Assessor 1A — already in `raw_assessor_parcels` schema (field `tax_code`)? Actually, looking at the schema, `tax_code` is *not* in `raw_assessor_parcels` today (we store `tax_tif_district_*` but not `tax_code`). Need to add it to the 1A fetch.
- **What it unlocks:** replaces the citywide 6.717 % with per-PIN composite rate. `estimated_annual_tax` becomes accurate. Tax-burden signals (tax_increase_pct_1yr / 5yr) get more meaningful per-parcel.
- **Effort:** small. New fetch module mirroring the existing assessor sources, plus add `tax_code` to 1A pull, plus update `pipeline/tax.py` to look up rate by tax_code.
- **Per-parcel:** yes, joins by tax_code.

#### 3. Building footprints with current stories / year_built / units

**The Chicago dataset is not actually current.** `hz9b-7nh8` ("Building Footprints (current)") is just a *map view*; its parent tabular dataset is `syp8-uezg`, which is the same data as `ssaf-e4ub`. I sampled both. Empirically:

| Dataset | Sampled rows in LP/LV bbox | `max(year_built)` | `max(edit_date)` |
|---|---|---|---|
| `syp8-uezg` (parent of `hz9b-7nh8`) | 2,000 | **2011** | 2015-02-27 |
| `ssaf-e4ub` (via map view `tf32-rk4u`) | 2,000 | **2010** | 2010-04-20 |

The "Last updated 2024/2025" label on the portal page is a metadata touch (`viewLastModified`); the actual rowdata epoch (`rowsUpdatedAt = 1439602534`) decodes to **2015-08-15**, and no row in the bbox has a `year_built` ≥ 2012. The 2014–2024 LP/Lakeview construction boom is invisible in this dataset. Bbox sample population rates were ~40 % `bldg_sq_fo`, ~57 % `year_built`, ~48 % `stories`, ~45 % `no_of_unit`. Where it does have a value, `bldg_sq_fo` agrees closely with assessor `building_sf` (median ratio 1.000 on the overlap, ±10 % on 62 %). 28.7 % of matched PINs have ≥2 footprints, so any backfill must aggregate footprints per PIN before writing.

**Three options, in priority order:**

**(a) Overture Maps Buildings — the actually-current alternative.** The Overture Maps Foundation publishes a conflated global buildings layer (OSM + Esri Community Maps + Google Open Buildings + Microsoft ML rooflines), refreshed monthly. GeoParquet on S3, ODbL, free. URL: <https://docs.overturemaps.org/guides/buildings/>. **Has `height` and `num_floors`; does NOT have `year_built` or `no_of_units`.** For our use case it gives us a current building envelope (footprint area × num_floors → building_sf proxy) where Cook County's chars dataset is empty, but doesn't give year_built or units. Effort: medium — one-time DuckDB-from-S3 extract clipped to Cook County polygon, then refresh on whatever cadence we want.

**(b) Chicago `syp8-uezg` (the live tabular parent of `hz9b-7nh8`) as a 2010-flagged backfill.** Use it for `building_sf` only on parcels currently missing it, write a `building_sf_source = 'syp8-uezg-2015'` column so the staleness is visible, and don't write `year_built` from this source (it's just plain wrong for anything new). Backfill gain in the bbox is ~12 % of footprints (the rest already have assessor data). Lower effort than Overture but fundamentally a snapshot.

**(c) Hybrid.** Take `building_sf` from `syp8-uezg` first; for any parcel still missing it, fall back to Overture's `height` × footprint area / floor-height-assumption. Belt-and-braces; only worth the effort if (a) and (b) individually leave too many gaps.

**Recommendation:** start with (a) Overture for `num_floors` + `height`, which gives current data and replaces `no_stories` directly. **Skip `year_built` from any open-source footprint dataset for now** — none of them have a current value for Chicago. The closest current proxy for "is this a new building" is the assessor's permit dataset (filtering for NEW CONSTRUCTION permits in the past N years), once the matching fix from Stop the line §2 is in.

- **Per-parcel:** yes (spatial join, parcel centroid → footprint polygon).
- **What it unlocks:** `num_floors` and a `building_sf` proxy on most of the ~6,500 visible parcels currently missing building data, including condo buildings and 5xx/3xx commercial. `no_of_units` and a current `year_built` remain unsolved at the public-data level.

#### 3.b Building Footprints — IMPLEMENTED in this branch as `syp8-uezg`

Per follow-up direction, the pipeline now pulls `syp8-uezg` (the live tabular parent of `hz9b-7nh8`) and merges it with the assessor characteristics per these rules:

- **For `year_built`, `building_sf`, `unit_count`:** if the assessor reports a `year_built > 2015`, keep the assessor value (footprints can't have it, by definition — the dataset is frozen). Otherwise prefer the footprint value when present, falling back to the assessor when the footprint missed the parcel.
- **For `condition`:** assessor wins when present (its 3-tier vocabulary is what the UI is built around). When assessor is NULL, translate the footprint's 4-tier `bldg_condi` to assessor language with two new bottom-of-scale values: `SOUND → Average`, `NEEDS MINOR REPAIR → Below Average`, `NEEDS MAJOR REPAIR → Poor`, `UNINHABITABLE → Uninhabitable`.
- **Multi-footprint parcels (28.7 % of bbox matches):** take the **largest-area** structure for all four fields. Garages and coach houses don't override the main building.
- **ACTIVE only:** drop rows with `bldg_statu != 'ACTIVE'` or non-null `demolished` date.
- **Condos:** spatial join lands the footprint on whichever unit PIN's centroid is closest, but writes get redirected via `is_condo_building` + `pin10` to the building rep PIN — single write per pin10, no double-counting in the rollup. The fetcher runs **after** `condo_rollup` in `fetch_all` so the rep flag is set.
- **Provenance columns:** `parcels.building_sf_source` and `parcels.condition_source` get tagged `'assessor'` or `'footprint'` so the staleness is visible per-row.

**Known gap:** post-2015 condo buildings have no public source for `year_built`, `building_sf`, or `unit_count` under these rules — the assessor's chars dataset doesn't cover condos, and `syp8-uezg` is frozen at 2010-2011. Those parcels stay NULL on those fields. There aren't many in LP/LV (most condo conversions in the bbox are pre-2010), but new construction since 2015 will show up as a class-299 building rep with NULL building data.

Not yet pulled: still need to actually run `cdp_building_footprints.fetch()` against the live DB after you sign off on the diff. Tests pass (158/158) against fixture data.

#### 4. Switch vacant-buildings dataset (`7nii-7srd` → `vauj-4grr`)

- **Dataset:** `vauj-4grr` ("311 - Vacant/Abandoned Building Complaints") confirmed live with current data. The current pipeline points at the historical/legacy dataset that ends in 2018.
- **What it unlocks:** keeps `has_vacancy_report` honest. Coverage in LP/LV will still be sparse (these aren't vacancy-prone neighborhoods) but at least it's current.
- **Effort:** trivial. Change one dataset ID, possibly remap field names.

#### 5. FEMA flood zones (NFHL)

- **Access:** ArcGIS REST at https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer (Layer 28 = SFHA polygons), or download the Cook County NFHL geodatabase from https://msc.fema.gov/portal/advanceSearch. Free.
- **What it unlocks:** flood-zone designation per parcel. Constraint signal for redevelopment (lender appetite, insurance, basement/garden-unit limits). Lakeview east of the Drive borders the lake; eastern edge of bbox has zone-AE exposure.
- **Effort:** small-medium. One spatial join, then a `flood_zone` column on parcels.
- **Per-parcel:** yes via spatial join.

### Tier 2 — useful, lower-priority, do after first scoring run

These add signal but don't materially change weights for the v1 use case.

- **Microsoft USBuildingFootprints** (https://github.com/microsoft/USBuildingFootprints) — Illinois GeoJSON, ~5.2M footprints, free, ODbL. **No heights, no year_built** — only polygons. Use as a coverage backstop *only if* the city footprints dataset proves unusable on a given parcel. Likely redundant given Tier 1 §3.
- **Chicago Landmark Districts (`zidz-sdfj`) and Individual Landmarks (`tdab-kixi`)** — confirmed live. Adds `is_landmark` / `landmark_district_name` per parcel. Strong negative signal for redevelopment (demolition restrictions; teardowns nearly impossible). LP and Lakeview have several districts (Old Town Triangle, Sheffield, Wrigley Field, etc.). Free Socrata, polygon spatial join.
- **HUD LIHTC database** — https://www.huduser.gov/portal/datasets/lihtc/property.html. Free CSV, annual. Adds `is_lihtc`, `lihtc_units`, `lihtc_year_placed_in_service`. Negative signal: LIHTC properties are under 30-year affordability covenants and are not realistic acquisition targets unless someone's buying for the tax credits. Smallish list in LP/LV but worth flagging.
- **Chicago ARO Rentals (`wyrz-5mk7`)** — point list of buildings already containing ARO units. Property-level signal that the building has affordable obligations. Adds `has_aro_units`, `aro_unit_count`. Free Socrata. Note: no polygon dataset of ARO Pilot **Areas** exists on the portal — the city only publishes those as PDFs / interactive maps. Skip the area boundaries unless we want to digitize them.
- **CTA L Ridership by Station (`5neh-572f`)** — confirmed. Daily entries 2001–present. Could populate `cta_nearest_station_avg_daily_rides` to differentiate Belmont (40k+/day) from Diversey (much lower). Better TOD-strength proxy than raw distance. Free Socrata. Tier 2 because the existing distance-only field is good enough for v1.
- **CTA bus stops (GTFS)** — full GTFS at https://www.transitchicago.com/downloads/sch_data/google_transit.zip; the data-portal mirror `sp6w-yusg` exists. Adds `nearest_bus_stop_dist_ft`, `bus_routes_within_quarter_mile`. Useful because Chicago's Connected Communities Ordinance gives bus-corridor TOD bonuses too, not just rail. Tier 2 because rail is the bigger driver in LP/LV and we already have it.
- **Census ACS block-group demographics** — via the `census` Python package or https://api.census.gov. Free with key. Adds median income, owner-occupancy rate, age distribution at block-group level (~1500 people). Neighborhood-quality / context signal, not per-parcel. Tier 2 because LP/LV is uniformly affluent — variation is small inside the bbox. Would matter much more if we expanded south/west.
- **Cook County Recorder bulk file (`4f2q-h3b7`)** — exists on the data portal but **only covers 2013–March 2015**. Static. Could mine it once for older mortgage / foreclosure history but it's stale by a decade. Low utility.
- **Chicago Affordable Housing Locations (`s6ha-ppgi`)** — different from LIHTC, points to subsidized buildings. Negative signal, similar to LIHTC. Easy add.

### Skip (or defer indefinitely)

- **Current foreclosure / mortgage / lien filings (Cook County Clerk Recorder portal)** — no free bulk feed. The legacy `4f2q-h3b7` ends in 2015. Per-document scraping at https://www.cookcountyclerkil.gov/recordings/search-recordings is fragile and the volume to keep current would be brutal. Vendors (PropertyShark, ATTOM, DataTree) sell it; budget would be hundreds to thousands per month. **Skip until the pipeline has revenue justifying a vendor cost.** Institute for Housing Studies at DePaul (https://www.housingstudies.org/data-portal/foreclosures/) publishes derived analyses if we just want neighborhood-level foreclosure stats.
- **eTOD / Connected Communities boundaries** — not on the data portal. Lives only as a layer in the city's interactive zoning map (https://gisapps.chicago.gov/ZoningMapWeb/). Could be derived from CTA stop locations + the ¼-mile / ½-mile buffer rules in the 2022 Connected Communities Ordinance — that derivation is straightforward in Python and gets us the same answer, so don't try to scrape the city map. Implement as a derived flag once we have CTA stops.
- **MLS / LoopNet / CoStar / Crexi** — paid or ToS-prohibited. Stick with the planned Zillow scrape for the ~50 parcels per wave that need a listing check.
- **Chicago Crime (`ijzp-q8t2`)**, **Chicago Public Schools**, **Chicago Parks** — neighborhood-context signals. Variation inside LP/LV is small enough that the signal-to-effort ratio is poor for v1. Skip; revisit if the geography expands.
- **Illinois SOS bulk LLC data** — still scrape-only as of April 2026; bulk download remains prohibited. The deferred enrichment-stage scrape (Source 4 in the spec) is the right scope. Paid alternatives: Cobalt Intelligence ($$), CompanyData.com (~$0.10/lookup). Don't try to bulk-pull beneficial owners pre-scoring.
- **Microsoft / Google global building footprints** — both lack height and year_built; redundant with Chicago's footprint dataset for our geography. Skip unless we expand outside the city limits.
- **Cook County Treasurer per-PIN scrape for tax bills** — duplicative with our 1D + Tier 1 §1 + Tier 1 §2 plan. Skip.

---

## Section D-bis — Broader real-estate data scan (added 2026-04-27)

This is the wider scan of property-specific public real-estate data beyond the Chicago/Cook ecosystem. Sources I'd actually use are pulled into the Tier-1/Tier-2 lists below; sources I researched and rejected are summarized at the end.

### Add to Tier 1

- **Chicago demolition permits (`e4xk-pud8`)** on data.cityofchicago.org. Subset of the existing permits dataset filtered to wrecking/demolition. Lagging-indicator of comp-set activity in the trade area — when comparable buildings nearby are getting torn down, the parcel's optionality goes up. Free Socrata, S effort. (Already in `raw_cdp_permits` if we filter `permit_type LIKE 'PERMIT - WRECKING%'`; this is a code-only addition, not a new fetch.)
- **Chicago 311 service requests (current unified dataset, `v6vf-nfxy`)** on data.cityofchicago.org. Beyond the vacant-building feed, filter on Sanitation Code Violation, Rodent Baiting, Garbage Cart Maintenance, Tree Debris, Graffiti Removal — aggregate by address over a 24-month window for a composite "neighborhood-grade" / "owner-attentiveness" signal per parcel. Free Socrata, M effort (one new fetch + per-parcel aggregation).
- **CFPB HMDA Modified LAR.** Annual mortgage application/origination dataset, free, national. Critical caveat: **the public version is geocoded to census tract only, not address.** So this is a tract-level capital-flow signal, not a per-parcel signal. Useful for context (refi volume / denial rates per tract) when comparing two parcels in different tracts; not for primary scoring. URL: <https://ffiec.cfpb.gov/data-publication/modified-lar>.
- **HUD Fair Market Rents (FMR) API.** Annual, free with token; Small-Area FMRs by ZIP for the Chicago MSA. Gives an underwriting rent-floor by ZIP for 1–4BR. Pair with ACS gross-rent at tract for context. Useful because it's a *current* per-ZIP rent number — none of our other sources have rents.

### Add to Tier 2

- **Chicago Energy Benchmarking dataset (`xq83-jr8c`).** ENERGY STAR scores and GHG intensity for buildings >50k SF. Covers ~1 % of Chicago's stock but a meaningful fraction of large multifamily, and identifies older inefficient buildings ripe for value-add. Free Socrata, S effort. Tier 2 only because the bbox subset is small and skewed toward bigger buildings than your initial deal profile.
- **Cook County DMF (deceased owner) cross-match.** SSA Death Master File public version is free and has a 3-year delay (or full DMF via NTIS for ~$1k/yr). Match on owner name → flag deceased owners, who are statistically motivated sellers (estate/probate). M effort.
- **Chicago Lead Service Line Inventory** (https://sli.chicagowaterquality.org/). Per-address material classification, ~492k records. Pre-1986 plumbing capex liability — relevant for pro-forma underwriting and a future seller-disclosure liability. M effort to get the underlying data (lookup tool exists; bulk extract may need FOIA).
- **National Register of Historic Places shapefile** from NPS. Federal historic-tax-credit eligibility overlay — different from Chicago landmarks. Free download, S effort. <https://www.nps.gov/subjects/nationalregister/data-downloads.htm>.
- **Zillow Observed Rent Index (ZORI) / Apartment List rent estimates.** Free at ZIP/metro level with attribution. Trend overlays for context, not per-parcel.

### Notable rejections from this pass

- **FinCEN Beneficial Ownership Information (BOI).** Treasury's March 2025 interim final rule **removed BOI reporting requirements for all US-formed entities and US persons.** Only foreign entities registered to do business in IL still file. The "ride the CTA bulk feed to find LLC owners" plan is dead at the federal level. Stuck with IL SOS scrape for owner-mapping. <https://www.fincen.gov/news/news-releases/fincen-removes-beneficial-ownership-reporting-requirements-us-companies-and-us>
- **Cook County Circuit Court bulk dockets (foreclosures, lis pendens).** Bulk access requires Chief Judge approval under IL Supreme Court electronic-access policy. No public API. Per-document scraping is the only zero-cost path. Realistic answer: pay a vendor (PropertyShark, ATTOM, BatchData) once revenue justifies it.
- **PACER federal court records.** Only useful for federal bankruptcies / federal tax liens, narrow signal, per-page fees. Skip.
- **Microsoft GlobalMLBuildingFootprints standalone.** Redundant with Overture, which already conflates it.
- **Google Open Buildings.** Africa/Asia/LATAM-focused; US coverage is sparse and folded into Overture.
- **OpenSanctions / OpenOwnership.org** for US LLCs. State filings don't disclose beneficial owners; coverage is essentially nil for our use case.
- **Chicago Lobbyist Registration / Ethics filings.** Cute for the "who's getting variances" question but not an acquisition-sourcing signal.
- **EPA ECHO / Superfund.** Not relevant for LP/Lakeview at parcel level.
- **BACP / liquor / business licenses.** Useful for commercial mixed-use sourcing only; skip for the multifamily-redevelopment v1.

### Self-correction note

The second research pass claimed there's a free monthly bulk delinquent-tax file from the Cook County Clerk. That contradicts the first pass and I couldn't independently verify the URL. Treat it as unproven. The Stop the line §1 access path is still an open decision: targeted scrape of the search portal (~21 hr/run for the bbox) vs. FOIA / data purchase from the Clerk's office.

---

## Section D — Do-this-before-scoring shortlist (ruthless)

1. **Fix `tax_delinquent`** by deciding on access path (targeted scrape of taxdelinquent.cookcountyclerkil.gov vs. FOIA/data purchase) and implementing it (Tier 1 §1). The scoring system is built around this signal; without it, weight calibration is invalid. **Open decision.**
2. **Fix permit and violation matching** (Stop the line §2). **Done in this branch** — `MATCH_RADIUS_FT` bumped from 50 ft → 200 ft in `sources/cdp_permits.py` and `sources/cdp_violations.py`. Re-run those two fetches against the existing raw tables to re-attach permits/violations to PINs.
3. **Pull Overture Maps Buildings** (Tier 1 §3a) for current `num_floors` / `height` on parcels missing assessor building data. Skip `year_built` from open-source footprints — Chicago's dataset is frozen at 2010 and Overture doesn't provide it. Use NEW CONSTRUCTION permits (post-fix #2) as the current-year-built proxy.
4. **Pull Cook County Clerk tax-code rates `9sqg-vznj`** and add `tax_code` to the 1A fetch. Without this, tax-burden signals are noisy averages.
5. **Switch 311 vacancy dataset** from `7nii-7srd` (legacy, ends 2018) to `vauj-4grr` (current). Trivial; do it now.
6. **Reframe `appeal_count`** from lifetime → windowed (e.g., last 3 yrs or ≥3 appeals in 5 yrs). Code-only.
7. **Spot-check `is_absentee`** on a sample of condo buildings to confirm we're not over-firing because of property-management mailing addresses.
8. **Fix mistyped zone strings.** **Done in this branch** — `_normalize_zone_class` in `sources/cdp_zoning.py` hyphenates `RS|RT|RM` + digit (covers the 13 polygons currently slipping through). Re-run cdp_zoning fetch.

Items 1, 3, 4, 5 are net-new fetching; 6–7 are local code-only fixes; 2 and 8 are landed in this branch and just need the affected fetches re-run.

Until items 1 and 2 are settled, weight calibration via the historical-analysis script will produce misleading weights because the underlying features are partially or wholly missing.

---

## Section E — One-paragraph "things to skip and why"

Skip the Cook County Recorder document-by-document scrape, all paid-vendor distress data (PropertyShark / ATTOM / DataTree / CoStar / LoopNet), eTOD-area scraping (derive it from CTA buffers instead), Microsoft USBuildingFootprints (redundant with the Chicago dataset and lacks heights), Census/ACS demographics (too uniform inside LP/LV to add signal), Chicago Crime / Schools / Parks (same — neighborhood-context noise at this geography), and Illinois SOS bulk pulls (still prohibited; the planned enrichment-stage scrape is the right scope). All of these either cost money the project can't justify pre-revenue, deliver signal that doesn't vary inside the bbox, or are redundant with what's already on the Tier 1/2 list.

---

## Reproduction SQL

Run from the repo root:

```bash
.venv/bin/python -c "import sqlite3; c=sqlite3.connect('data/full.db'); c.row_factory=sqlite3.Row"
```

or via `sqlite3 data/full.db`:

```sql
-- Total parcels
SELECT COUNT(*) FROM parcels;

-- Headline population rates
SELECT
  ROUND(100.0 * SUM(CASE WHEN tax_delinquent IS NOT NULL THEN 1 ELSE 0 END) / COUNT(*), 1) AS tax_delinquent_pct,
  ROUND(100.0 * SUM(CASE WHEN open_violations_count > 0       THEN 1 ELSE 0 END) / COUNT(*), 1) AS open_viol_pct,
  ROUND(100.0 * SUM(CASE WHEN years_since_last_permit IS NOT NULL THEN 1 ELSE 0 END) / COUNT(*), 1) AS permit_pct,
  ROUND(100.0 * SUM(CASE WHEN building_sf > 0 THEN 1 ELSE 0 END) / COUNT(*), 1) AS building_sf_all,
  ROUND(100.0 * SUM(CASE WHEN building_sf > 0 AND is_condo_unit=0 THEN 1 ELSE 0 END)
        / SUM(CASE WHEN is_condo_unit=0 THEN 1 ELSE 0 END), 1) AS building_sf_visible
FROM parcels;

-- Building data by class group
SELECT
  CASE substr(property_class,1,1)
    WHEN '2' THEN '2xx residential'
    WHEN '3' THEN '3xx commercial/multifamily'
    WHEN '5' THEN '5xx commercial/industrial'
    ELSE substr(property_class,1,1)||'xx other'
  END AS cls,
  COUNT(*) AS n,
  ROUND(100.0 * SUM(CASE WHEN building_sf > 0 THEN 1 ELSE 0 END) / COUNT(*), 1) AS bldg_sf_pct
FROM parcels
GROUP BY cls
ORDER BY n DESC;

-- Zone-class coverage of FAR/density lookup
SELECT
  CASE
    WHEN zone_class LIKE 'RM-%' THEN 'RM-x'
    WHEN zone_class LIKE 'RT-%' THEN 'RT-x'
    WHEN zone_class LIKE 'RS-%' THEN 'RS-x'
    WHEN zone_class LIKE 'B%'   THEN 'B-x'
    WHEN zone_class LIKE 'C%'   THEN 'C-x'
    WHEN zone_class LIKE 'M%'   THEN 'M-x'
    WHEN zone_class LIKE 'PD%'  THEN 'PD'
    ELSE zone_class
  END AS zg,
  COUNT(*) AS n,
  ROUND(100.0 * SUM(CASE WHEN max_far IS NOT NULL THEN 1 ELSE 0 END) / COUNT(*), 1) AS max_far_pct
FROM parcels
WHERE is_condo_unit = 0
GROUP BY zg
ORDER BY n DESC;

-- Confirm clerk_delinquent stub
SELECT COUNT(*) FROM raw_clerk_delinquent;       -- expect 0

-- Permit / violation match counts (the leak)
SELECT COUNT(DISTINCT property_group) FROM raw_cdp_violations
 WHERE violation_status LIKE 'OPEN%';            -- thousands
SELECT COUNT(*) FROM parcels WHERE open_violations_count > 0;  -- ~930
```
