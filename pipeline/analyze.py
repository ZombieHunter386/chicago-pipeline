"""Historical-analysis script that derives initial scoring weights.

Looks at parcels that *did* get NEW CONSTRUCTION or WRECKING/DEMOLITION permits
(positives) vs. parcels that didn't (negatives) inside the target geography,
fits a logistic regression on z-scored continuous + raw binary features, and
emits config/scoring.yaml + a markdown analysis report.
"""
from __future__ import annotations
from datetime import datetime, UTC
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from sklearn.linear_model import LogisticRegression

from pipeline.config import GeographyConfig
from pipeline.db import get_connection
from pipeline.spatial import (
    DEFAULT_GEO_RADIUS_FT,
    match_records_to_parcels_with_address,
)


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


def _is_pd_zone(zone_class: str | None) -> bool:
    if not zone_class:
        return False
    return zone_class.strip().upper().startswith("PD")


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

    if not eligible:
        df = pd.DataFrame(columns=["pin", "label"] + columns)
        df.attrs["imputation_rates"] = {}
        df.attrs["funnel"] = funnel
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
    df.attrs["funnel"] = funnel
    return df


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
