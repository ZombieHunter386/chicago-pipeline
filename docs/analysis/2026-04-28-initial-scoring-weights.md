# Initial Scoring Weights — Lincoln Park / Lakeview / adjacent

- **Version:** `1.1.0-2026-04-29`
- **Generated at:** 2026-04-29T00:21:45+00:00
- **DB:** `data/full.db`
- **Positive examples (qualifying permits 2006-present):** 3,395

## Eligibility funnel

| Step | Parcels remaining |
|---|---|
| Total parcels in DB | 67,677 |
| After dropping tax-exempt | 66,767 |
| After dropping no-zone-class | 66,767 |
| After dropping PD-zoned | 61,042 |
| After dropping condo units | 20,557 |
| After dropping constituents of training groups | 17,925 |
| Consolidation groups added | +6,864 |
| Training set total | **24,789** |

## Imputation rates

Continuous NULLs imputed with the training-set median; binary NULLs imputed with 0.

| Signal | n imputed | % of training set |
|---|---|---|
| lot_size_sf | 3,444 | 13.9% |
| hold_duration_years | 6,551 | 26.4% |
| max_far | 882 | 3.6% |
| far_gap_delta | 11,838 | 47.8% |
| land_building_ratio | 182 | 0.7% |
| estimated_annual_tax | 156 | 0.6% |
| tax_increase_pct_5yr | 1,969 | 7.9% |
| cta_distance_ft | 0 | 0.0% |
| appeal_count | 4,869 | 19.6% |
| open_violations_count | 23,962 | 96.7% |
| years_since_last_permit | 23,061 | 93.0% |
| vacant_violations_count | 24,789 | 100.0% |
| scofflaw_appearances_count | 24,789 | 100.0% |
| is_absentee | 0 | 0.0% |
| is_llc | 0 | 0.0% |
| allows_multifamily_by_right | 1 | 0.0% |
| is_scofflaw | 17,925 | 72.3% |

## Per-signal distribution: positive vs. negative

| Signal | Kind | n+ | n- | Pos mean | Neg mean | Pos med | Neg med | Pos rate | Neg rate |
|---|---|---|---|---|---|---|---|---|---|
| lot_size_sf | continuous | 2,801 | 21,988 | 6980.6512 | 23562.0618 | 3235.8321 | 3692.2226 | — | — |
| hold_duration_years | continuous | 2,801 | 21,988 | 8.0873 | 10.34 | 7.64 | 8.65 | — | — |
| max_far | continuous | 2,801 | 21,988 | 1.3531 | 1.9741 | 1.05 | 1.2 | — | — |
| far_gap_delta | continuous | 2,801 | 21,988 | 0.1657 | 0.2615 | 0.1 | 0.2063 | — | — |
| land_building_ratio | continuous | 2,801 | 21,988 | 0.3926 | 0.357 | 0.3145 | 0.3493 | — | — |
| estimated_annual_tax | continuous | 2,801 | 21,988 | 50880.9752 | 56790.3567 | 33676.61 | 18959.19 | — | — |
| tax_increase_pct_5yr | continuous | 2,801 | 21,988 | 133976.8976 | 29961.0161 | 39.048 | 29.9785 | — | — |
| cta_distance_ft | continuous | 2,801 | 21,988 | 2169.5879 | 2153.1568 | 1962.8 | 2033.05 | — | — |
| appeal_count | continuous | 2,801 | 21,988 | 3.3285 | 3.7387 | 3.0 | 3.0 | — | — |
| open_violations_count | continuous | 2,801 | 21,988 | 4.1192 | 4.0893 | 4.0 | 4.0 | — | — |
| years_since_last_permit | continuous | 2,801 | 21,988 | 7.6146 | 7.6475 | 7.59 | 7.59 | — | — |
| vacant_violations_count | continuous | 2,801 | 21,988 | 0.0 | 0.0 | 0.0 | 0.0 | — | — |
| scofflaw_appearances_count | continuous | 2,801 | 21,988 | 0.0 | 0.0 | 0.0 | 0.0 | — | — |
| is_absentee | binary | 2,801 | 21,988 | — | — | — | — | 0.4448 | 0.5708 |
| is_llc | binary | 2,801 | 21,988 | — | — | — | — | 0.2078 | 0.1483 |
| allows_multifamily_by_right | binary | 2,801 | 21,988 | — | — | — | — | 0.5634 | 0.7166 |
| is_scofflaw | binary | 2,801 | 21,988 | — | — | — | — | 0.0 | 0.0 |

## Logistic regression results

Continuous features are z-scored before fitting so coefficients are comparable. 95% CIs are bootstrap (200 iterations, sample-with-replacement). A signal is **significant** when its 95% CI does not cross 0; insignificant signals get weight 0 and are not used in the score.

| Signal | Coef | 95% CI | Significant | Direction | Weight |
|---|---|---|---|---|---|
| lot_size_sf | -1.510 | [-2.491, -0.842] | yes | negative | 0.397 |
| hold_duration_years | -0.358 | [-0.407, -0.314] | yes | negative | 0.094 |
| max_far | -0.598 | [-0.706, -0.509] | yes | negative | 0.158 |
| far_gap_delta | -0.067 | [-0.151, -0.016] | yes | negative | 0.018 |
| land_building_ratio | 0.025 | [-0.024, 0.072] | **no** | positive | 0.000 |
| estimated_annual_tax | 0.071 | [0.017, 0.119] | yes | positive | 0.019 |
| tax_increase_pct_5yr | 0.059 | [-1.144, 0.102] | **no** | positive | 0.000 |
| cta_distance_ft | 0.049 | [0.009, 0.105] | yes | positive | 0.013 |
| appeal_count | 0.106 | [-0.089, 0.393] | **no** | positive | 0.000 |
| open_violations_count | 0.052 | [-0.018, 0.127] | **no** | positive | 0.000 |
| years_since_last_permit | -0.025 | [-0.075, 0.027] | **no** | negative | 0.000 |
| vacant_violations_count | 0.000 | [0.000, 0.000] | **no** | positive | 0.000 |
| scofflaw_appearances_count | 0.000 | [0.000, 0.000] | **no** | positive | 0.000 |
| is_absentee | -0.230 | [-0.323, -0.147] | yes | negative | 0.061 |
| is_llc | 0.642 | [0.518, 0.772] | yes | positive | 0.169 |
| allows_multifamily_by_right | -0.274 | [-0.365, -0.171] | yes | negative | 0.072 |
| is_scofflaw | 0.000 | [0.000, 0.000] | **no** | positive | 0.000 |

## Top 5 signals by weight magnitude

1. **lot_size_sf** — weight 0.397, direction negative
2. **is_llc** — weight 0.169, direction positive
3. **max_far** — weight 0.158, direction negative
4. **hold_duration_years** — weight 0.094, direction negative
5. **allows_multifamily_by_right** — weight 0.072, direction negative

## Caveats

- **Snapshot fidelity:** v1 uses the *current* parcels table for all features, not a per-PIN reconstructed pre-development snapshot. Most signals (zoning class, lot_size_sf, cta_distance_ft, is_llc) don't change materially year-to-year; signals that do (hold_duration_years, assessed-value trends) are biased toward the post-event state. Document the bias direction; refine in a future iteration if a signal's weight looks suspiciously high.
- **`tax_delinquent` excluded entirely:** the Cook County Clerk delinquent-tax CSV referenced in the data-sources spec is a header-only stub on `data/full.db` (see `docs/analysis/2026-04-27-data-source-audit.md` §1). The strongest motivation-to-sell signal in the literature is missing. The model will under-weight motivation as a result; decide on access path (targeted scrape vs FOIA) before re-running.
- **`has_vacancy_report` excluded:** the configured 311 dataset (`7nii-7srd`) is a defunct legacy feed that ends in 2018; switching to `vauj-4grr` is on the audit's Tier-1 do-list.
- **Condo + commercial building data gap:** `building_sf`, `year_built`, `condition`, `built_far` are excluded from features because ~78% of all parcels (and ~35% of the non-condo-unit subset) lack values. This will improve once the building-footprints merge from the audit branch is run against the live DB.
- **`open_violations_count` and `years_since_last_permit` are sparse:** the address-first matcher fix shipped in this branch hasn't been re-run on `data/full.db` yet at training time. Re-run those two fetches and re-run analyze for tighter CIs.
- **`appeal_count` is too coarse:** ~80% of parcels show ≥1 lifetime appeal. The audit recommends windowing this to last-3-years; v1 uses lifetime as-is.
- **`is_absentee` is over-firing on condo buildings:** 54.5% true population-wide. The condo-unit drop in the eligibility funnel removes most of the false positives, but the building-rep PINs (mailed to property managers) likely still over-fire.