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
