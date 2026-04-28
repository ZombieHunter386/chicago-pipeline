# Initial Scoring Weights — Lincoln Park / Lakeview / adjacent

- **Version:** `1.0.0-2026-04-28`
- **Generated at:** 2026-04-28T21:19:51+00:00
- **DB:** `data/full.db`
- **Positive examples (qualifying permits 2006-present):** 3,395

## Eligibility funnel

| Step | Parcels remaining |
|---|---|
| Total parcels in DB | 67,677 |
| After dropping tax-exempt | 66,767 |
| After dropping no-zone-class | 66,767 |
| After dropping PD-zoned | 61,042 |
| After dropping condo units | **20,557** (training set) |

## Imputation rates

Continuous NULLs imputed with the training-set median; binary NULLs imputed with 0.

| Signal | n imputed | % of training set |
|---|---|---|
| lot_size_sf | 3,062 | 14.9% |
| hold_duration_years | 5,985 | 29.1% |
| max_far | 6 | 0.0% |
| far_gap_delta | 7,169 | 34.9% |
| land_building_ratio | 104 | 0.5% |
| estimated_annual_tax | 76 | 0.4% |
| tax_increase_pct_5yr | 1,434 | 7.0% |
| cta_distance_ft | 0 | 0.0% |
| appeal_count | 4,573 | 22.2% |
| open_violations_count | 19,760 | 96.1% |
| years_since_last_permit | 18,869 | 91.8% |
| vacant_violations_count | 20,557 | 100.0% |
| scofflaw_appearances_count | 20,557 | 100.0% |
| is_absentee | 0 | 0.0% |
| is_llc | 0 | 0.0% |
| allows_multifamily_by_right | 1 | 0.0% |
| is_scofflaw | 20,557 | 100.0% |

## Per-signal distribution: positive vs. negative

| Signal | Kind | n+ | n- | Pos mean | Neg mean | Pos med | Neg med | Pos rate | Neg rate |
|---|---|---|---|---|---|---|---|---|---|
| lot_size_sf | continuous | 2,751 | 17,806 | 4045.6673 | 4619.1899 | 3130.9971 | 3130.9971 | — | — |
| hold_duration_years | continuous | 2,751 | 17,806 | 8.1056 | 10.8112 | 7.55 | 9.17 | — | — |
| max_far | continuous | 2,751 | 17,806 | 1.3453 | 1.591 | 1.05 | 1.2 | — | — |
| far_gap_delta | continuous | 2,751 | 17,806 | 0.1544 | 0.2425 | 0.0876 | 0.2034 | — | — |
| land_building_ratio | continuous | 2,751 | 17,806 | 0.4061 | 0.4248 | 0.3158 | 0.4226 | — | — |
| estimated_annual_tax | continuous | 2,751 | 17,806 | 40012.2158 | 64596.5094 | 32472.51 | 22916.78 | — | — |
| tax_increase_pct_5yr | continuous | 2,751 | 17,806 | 100.0888 | 5399.6367 | 39.7011 | 33.3144 | — | — |
| cta_distance_ft | continuous | 2,751 | 17,806 | 2194.2851 | 2067.2711 | 1978.6 | 1823.8 | — | — |
| appeal_count | continuous | 2,751 | 17,806 | 2.9909 | 2.9542 | 3.0 | 3.0 | — | — |
| open_violations_count | continuous | 2,751 | 17,806 | 4.0349 | 4.1074 | 4.0 | 4.0 | — | — |
| years_since_last_permit | continuous | 2,751 | 17,806 | 7.6568 | 7.691 | 7.62 | 7.62 | — | — |
| vacant_violations_count | continuous | 2,751 | 17,806 | 0.0 | 0.0 | 0.0 | 0.0 | — | — |
| scofflaw_appearances_count | continuous | 2,751 | 17,806 | 0.0 | 0.0 | 0.0 | 0.0 | — | — |
| is_absentee | binary | 2,751 | 17,806 | — | — | — | — | 0.4475 | 0.5852 |
| is_llc | binary | 2,751 | 17,806 | — | — | — | — | 0.2145 | 0.1801 |
| allows_multifamily_by_right | binary | 2,751 | 17,806 | — | — | — | — | 0.5729 | 0.7031 |
| is_scofflaw | binary | 2,751 | 17,806 | — | — | — | — | 0.0 | 0.0 |

## Logistic regression results

Continuous features are z-scored before fitting so coefficients are comparable. 95% CIs are bootstrap (200 iterations, sample-with-replacement). A signal is **significant** when its 95% CI does not cross 0; insignificant signals get weight 0 and are not used in the score.

| Signal | Coef | 95% CI | Significant | Direction | Weight |
|---|---|---|---|---|---|
| lot_size_sf | 0.005 | [-0.033, 0.513] | **no** | positive | 0.000 |
| hold_duration_years | -0.441 | [-0.498, -0.399] | yes | negative | 0.149 |
| max_far | -0.224 | [-0.291, -0.159] | yes | negative | 0.076 |
| far_gap_delta | -0.061 | [-0.387, -0.001] | yes | negative | 0.021 |
| land_building_ratio | -0.116 | [-0.167, -0.074] | yes | negative | 0.039 |
| estimated_annual_tax | -0.575 | [-0.995, -0.404] | yes | negative | 0.194 |
| tax_increase_pct_5yr | -0.476 | [-0.671, -0.165] | yes | negative | 0.161 |
| cta_distance_ft | 0.100 | [0.065, 0.145] | yes | positive | 0.034 |
| appeal_count | 0.030 | [-0.008, 0.074] | **no** | positive | 0.000 |
| open_violations_count | -0.043 | [-0.125, 0.012] | **no** | negative | 0.000 |
| years_since_last_permit | -0.012 | [-0.070, 0.036] | **no** | negative | 0.000 |
| vacant_violations_count | 0.000 | [0.000, 0.000] | **no** | positive | 0.000 |
| scofflaw_appearances_count | 0.000 | [0.000, 0.000] | **no** | positive | 0.000 |
| is_absentee | -0.242 | [-0.338, -0.158] | yes | negative | 0.082 |
| is_llc | 0.454 | [0.320, 0.545] | yes | positive | 0.153 |
| allows_multifamily_by_right | -0.274 | [-0.368, -0.159] | yes | negative | 0.092 |
| is_scofflaw | 0.000 | [0.000, 0.000] | **no** | positive | 0.000 |

## Top 5 signals by weight magnitude

1. **estimated_annual_tax** — weight 0.194, direction negative
2. **tax_increase_pct_5yr** — weight 0.161, direction negative
3. **is_llc** — weight 0.153, direction positive
4. **hold_duration_years** — weight 0.149, direction negative
5. **allows_multifamily_by_right** — weight 0.092, direction negative

## Caveats

- **Snapshot fidelity:** v1 uses the *current* parcels table for all features, not a per-PIN reconstructed pre-development snapshot. Most signals (zoning class, lot_size_sf, cta_distance_ft, is_llc) don't change materially year-to-year; signals that do (hold_duration_years, assessed-value trends) are biased toward the post-event state. Document the bias direction; refine in a future iteration if a signal's weight looks suspiciously high.
- **`tax_delinquent` excluded entirely:** the Cook County Clerk delinquent-tax CSV referenced in the data-sources spec is a header-only stub on `data/full.db` (see `docs/analysis/2026-04-27-data-source-audit.md` §1). The strongest motivation-to-sell signal in the literature is missing. The model will under-weight motivation as a result; decide on access path (targeted scrape vs FOIA) before re-running.
- **`has_vacancy_report` excluded:** the configured 311 dataset (`7nii-7srd`) is a defunct legacy feed that ends in 2018; switching to `vauj-4grr` is on the audit's Tier-1 do-list.
- **Condo + commercial building data gap:** `building_sf`, `year_built`, `condition`, `built_far` are excluded from features because ~78% of all parcels (and ~35% of the non-condo-unit subset) lack values. This will improve once the building-footprints merge from the audit branch is run against the live DB.
- **`open_violations_count` and `years_since_last_permit` are sparse:** the address-first matcher fix shipped in this branch hasn't been re-run on `data/full.db` yet at training time. Re-run those two fetches and re-run analyze for tighter CIs.
- **`appeal_count` is too coarse:** ~80% of parcels show ≥1 lifetime appeal. The audit recommends windowing this to last-3-years; v1 uses lifetime as-is.
- **`is_absentee` is over-firing on condo buildings:** 54.5% true population-wide. The condo-unit drop in the eligibility funnel removes most of the false positives, but the building-rep PINs (mailed to property managers) likely still over-fire.