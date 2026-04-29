"""Tests for pipeline/analyze.py — the historical-analysis script that derives
initial scoring weights from permit history."""
from pathlib import Path
from datetime import datetime, UTC

import numpy as np
import pandas as pd
import yaml

from pipeline import analyze
from pipeline.db import init_db, upsert_rows


def test_signals_registry_shape():
    """SIGNALS is the single source of truth for what features the model sees.
    Every entry must be a 3-tuple of (column_name, kind, source_table) where
    kind is 'continuous' or 'binary'."""
    assert len(analyze.SIGNALS) > 0
    for entry in analyze.SIGNALS:
        assert len(entry) == 3
        col, kind, source = entry
        assert isinstance(col, str) and col
        assert kind in ("continuous", "binary")
        assert source == "parcels", \
            f"{col}: only the parcels table is supported in v1"


def test_signals_excludes_known_bad_columns():
    """tax_delinquent has 0% population on data/full.db (CSV is a stub).
    has_vacancy_report uses a defunct legacy dataset. Both must NOT be in
    the v1 feature set."""
    cols = [s[0] for s in analyze.SIGNALS]
    assert "tax_delinquent" not in cols
    assert "has_vacancy_report" not in cols


def test_analyze_entry_point_exists():
    """The orchestrator must accept (db_path, geo, scoring_yaml_path, report_md_path)."""
    import inspect
    sig = inspect.signature(analyze.analyze)
    assert list(sig.parameters) == ["db_path", "geo", "scoring_yaml_path", "report_md_path"]


def _build_analyze_db(tmp_path: Path, parcels: list[dict], permits: list[dict] | None = None,
                      values: list[dict] | None = None,
                      exempt: list[dict] | None = None) -> Path:
    """Create a fresh SQLite DB with init_db() schema and insert the given rows
    directly into raw_/parcels tables. No fetch flow — these tests don't care
    about Socrata wiring; they care about the analyze logic."""
    db_path = tmp_path / "analyze.db"
    init_db(db_path)
    now = datetime.now(UTC).isoformat(timespec="seconds")
    if parcels:
        upsert_rows(db_path, "parcels",
                    [{**p, "last_fetched_date": now} for p in parcels],
                    key_columns=["pin"])
    if permits:
        upsert_rows(db_path, "raw_cdp_permits",
                    [{**p, "fetched_at": now} for p in permits],
                    key_columns=["permit_number"])
    if values:
        upsert_rows(db_path, "raw_assessor_values",
                    [{**v, "fetched_at": now} for v in values],
                    key_columns=["pin", "year"])
    if exempt:
        upsert_rows(db_path, "raw_assessor_exempt",
                    [{**e, "fetched_at": now} for e in exempt],
                    key_columns=["pin"])
    return db_path


def test_identify_positive_examples_filters_permit_types(tmp_path):
    """Only NEW CONSTRUCTION and WRECKING/DEMOLITION qualify. Reroofs,
    renovations, and electrical permits don't move parcels into the positive
    set — those happen routinely on long-held property without redevelopment."""
    parcels = [
        {"pin": "14210010010000", "address": "100 W DIVERSEY PKWY",
         "lat": 41.94001, "lng": -87.65001},
        {"pin": "14210010020000", "address": "200 N HALSTED",
         "lat": 41.93001, "lng": -87.66001},
        {"pin": "14210010030000", "address": "300 W FULLERTON AVE",
         "lat": 41.92501, "lng": -87.65501},
    ]
    permits = [
        # PIN 1 — NEW CONSTRUCTION 2018, qualifies
        {"permit_number": "p1", "permit_type": "PERMIT - NEW CONSTRUCTION",
         "issue_date": "2018-05-12",
         "street_number": "100", "street_direction": "W", "street_name": "DIVERSEY PKWY",
         "latitude": 41.94001, "longitude": -87.65001},
        # PIN 1 — earlier demo 2014, qualifies and supersedes the 2018 one
        # (we want the EVENT year — earliest qualifying permit)
        {"permit_number": "p2", "permit_type": "PERMIT - WRECKING/DEMOLITION",
         "issue_date": "2014-08-22",
         "street_number": "100", "street_direction": "W", "street_name": "DIVERSEY PKWY",
         "latitude": 41.94001, "longitude": -87.65001},
        # PIN 2 — RENOVATION ONLY, does NOT qualify
        {"permit_number": "p3", "permit_type": "PERMIT - RENOVATION/ALTERATION",
         "issue_date": "2020-01-15",
         "street_number": "200", "street_direction": "N", "street_name": "HALSTED",
         "latitude": 41.93001, "longitude": -87.66001},
        # PIN 3 — ELECTRIC WIRING, does NOT qualify
        {"permit_number": "p4", "permit_type": "PERMIT - ELECTRIC WIRING",
         "issue_date": "2021-06-01",
         "street_number": "300", "street_direction": "W", "street_name": "FULLERTON AVE",
         "latitude": 41.92501, "longitude": -87.65501},
    ]
    db_path = _build_analyze_db(tmp_path, parcels, permits)

    result = analyze.identify_positive_examples(db_path)

    # Only PIN 1 qualifies, and the year is the earlier 2014 demo, not the 2018 build.
    assert result == {"14210010010000": 2014}


def test_identify_positive_examples_handles_address_match(tmp_path):
    """Permits with no lat/lng but matching street address should still match
    via the address-first matcher (Tier 1)."""
    parcels = [
        {"pin": "14210010010000", "address": "100 W DIVERSEY PKWY",
         "lat": 41.94001, "lng": -87.65001},
    ]
    permits = [
        # No lat/lng, but the street_number/direction/name reconstruct to the parcel address.
        {"permit_number": "p1", "permit_type": "PERMIT - NEW CONSTRUCTION",
         "issue_date": "2019-07-04",
         "street_number": "100", "street_direction": "W", "street_name": "DIVERSEY PKWY",
         "latitude": None, "longitude": None},
    ]
    db_path = _build_analyze_db(tmp_path, parcels, permits)
    result = analyze.identify_positive_examples(db_path)
    assert result == {"14210010010000": 2019}


def test_identify_positive_examples_returns_empty_when_no_qualifying_permits(tmp_path):
    parcels = [{"pin": "14210010010000", "address": "100 W DIVERSEY PKWY",
                "lat": 41.94001, "lng": -87.65001}]
    permits = [
        {"permit_number": "p1", "permit_type": "PERMIT - RENOVATION/ALTERATION",
         "issue_date": "2020-01-01",
         "street_number": "100", "street_direction": "W", "street_name": "DIVERSEY PKWY",
         "latitude": 41.94001, "longitude": -87.65001},
    ]
    db_path = _build_analyze_db(tmp_path, parcels, permits)
    assert analyze.identify_positive_examples(db_path) == {}


def _parcel_row(pin, **overrides):
    """Helper: minimum-viable parcels row with sensible defaults so individual
    tests only spell out the fields they care about."""
    base = {
        "pin": pin, "address": f"{pin[-4:]} W FAKE ST", "lat": 41.93, "lng": -87.65,
        "lot_size_sf": 5000.0, "hold_duration_years": 10.0,
        "max_far": 2.0, "far_gap_delta": 0.5, "land_building_ratio": 0.4,
        "estimated_annual_tax": 12000.0, "tax_increase_pct_5yr": 15.0,
        "cta_distance_ft": 2000.0, "appeal_count": 1, "open_violations_count": 0,
        "years_since_last_permit": 5.0, "vacant_violations_count": 0,
        "scofflaw_appearances_count": 0,
        "is_absentee": 0, "is_llc": 0, "allows_multifamily_by_right": 1,
        "is_scofflaw": 0, "is_condo_unit": 0, "zone_class": "RM-5",
    }
    base.update(overrides)
    return base


def test_build_training_table_basic_shape(tmp_path):
    parcels = [_parcel_row("14210010010000"), _parcel_row("14210010020000")]
    db_path = _build_analyze_db(tmp_path, parcels)
    positives = {"14210010010000": 2018}
    df = analyze.build_training_table(db_path, positives)
    assert len(df) == 2
    assert "label" in df.columns
    # Order isn't guaranteed; check by PIN.
    by_pin = df.set_index("pin")
    assert by_pin.loc["14210010010000", "label"] == 1
    assert by_pin.loc["14210010020000", "label"] == 0
    # Every signal column must be present.
    for col, _kind, _src in analyze.SIGNALS:
        assert col in df.columns
    assert df.attrs["funnel"]["total_parcels"] == 2
    assert df.attrs["funnel"]["after_condo_unit_drop"] == 2


def test_build_training_table_drops_tax_exempt(tmp_path):
    parcels = [_parcel_row("14210010010000"), _parcel_row("14210010020000")]
    exempt = [{"pin": "14210010020000", "exemption_type": "Religious"}]
    db_path = _build_analyze_db(tmp_path, parcels, exempt=exempt)
    df = analyze.build_training_table(db_path, positive_pins={})
    assert df["pin"].tolist() == ["14210010010000"]


def test_build_training_table_drops_pd_zoned(tmp_path):
    parcels = [
        _parcel_row("14210010010000", zone_class="RM-5"),
        _parcel_row("14210010020000", zone_class="PD 555", max_far=None,
                    allows_multifamily_by_right=None, far_gap_delta=None),
    ]
    db_path = _build_analyze_db(tmp_path, parcels)
    df = analyze.build_training_table(db_path, positive_pins={})
    assert df["pin"].tolist() == ["14210010010000"]


def test_build_training_table_drops_condo_units_keeps_building_reps(tmp_path):
    parcels = [
        _parcel_row("14210010010000", is_condo_unit=0),
        _parcel_row("14210010020000", is_condo_unit=1),
    ]
    db_path = _build_analyze_db(tmp_path, parcels)
    df = analyze.build_training_table(db_path, positive_pins={})
    assert df["pin"].tolist() == ["14210010010000"]


def test_build_training_table_imputes_nulls(tmp_path):
    """Continuous NULLs become the training-set median; binary NULLs become 0.
    Imputed cells are tracked so the report can disclose the imputation rate."""
    parcels = [
        _parcel_row("14210010010000", lot_size_sf=4000.0, is_absentee=1),
        _parcel_row("14210010020000", lot_size_sf=8000.0, is_absentee=None),
        _parcel_row("14210010030000", lot_size_sf=None,    is_absentee=None),
    ]
    db_path = _build_analyze_db(tmp_path, parcels)
    df = analyze.build_training_table(db_path, positive_pins={})
    # Median of (4000, 8000) = 6000
    by_pin = df.set_index("pin")
    assert by_pin.loc["14210010030000", "lot_size_sf"] == 6000.0
    # Binary NULLs go to 0
    assert by_pin.loc["14210010020000", "is_absentee"] == 0
    assert by_pin.loc["14210010030000", "is_absentee"] == 0
    # Imputation rate exposed via attrs (consumed by the report writer)
    rates = df.attrs["imputation_rates"]
    assert rates["lot_size_sf"]["pct"] == round(100 / 3, 1)  # 1 of 3 imputed
    assert rates["is_absentee"]["pct"] == round(200 / 3, 1)  # 2 of 3 imputed


def test_compare_distributions_continuous_and_binary(tmp_path):
    parcels = [
        _parcel_row(f"142100100{i:02d}0000",
                    lot_size_sf=4000.0 + i * 1000.0,
                    is_absentee=(1 if i % 2 == 0 else 0))
        for i in range(10)
    ]
    db_path = _build_analyze_db(tmp_path, parcels)
    positive_pins = {parcels[0]["pin"]: 2018, parcels[1]["pin"]: 2019}
    df = analyze.build_training_table(db_path, positive_pins)
    stats = analyze.compare_distributions(df)
    by_signal = {s["signal"]: s for s in stats}

    lot = by_signal["lot_size_sf"]
    assert lot["kind"] == "continuous"
    assert lot["n_positive"] == 2
    assert lot["n_negative"] == 8
    # Positives are the first two rows (4000, 5000) → mean 4500
    assert lot["positive_mean"] == 4500.0
    # Negatives are 5000–13000 step 1000 → mean 9000.0  (NOTE: pin 0 lot=4000 IS positive,
    # pin 1 lot=5000 IS positive — negatives are 6000..13000 → mean 9500)
    assert lot["negative_mean"] == 9500.0

    abs_ = by_signal["is_absentee"]
    assert abs_["kind"] == "binary"
    # Positives: pin 0 (i=0, abs=1), pin 1 (i=1, abs=0) → rate 0.5
    assert abs_["positive_rate"] == 0.5
    # Negatives: i in 2..9 → 4 absentee (even i: 2,4,6,8), 4 not → 0.5
    assert abs_["negative_rate"] == 0.5


def _build_separable_training_df(n_pos=40, n_neg=160, seed=0):
    """A synthetic dataframe where lot_size_sf strongly predicts label,
    is_llc weakly predicts, and cta_distance_ft is pure noise. Used to verify
    the regression actually picks out signal vs. noise."""
    rng = np.random.default_rng(seed)
    cols = [s[0] for s in analyze.SIGNALS]
    rows = []
    for label in [1] * n_pos + [0] * n_neg:
        row = {"pin": f"PIN{len(rows):05d}", "label": label}
        for col, kind, _ in analyze.SIGNALS:
            if col == "lot_size_sf":
                row[col] = rng.normal(8000 if label else 4000, 500)
            elif col == "is_llc":
                row[col] = int(rng.random() < (0.6 if label else 0.4))
            elif kind == "continuous":
                row[col] = rng.normal(0, 1)  # noise
            else:
                row[col] = int(rng.random() < 0.5)  # noise
        rows.append(row)
    df = pd.DataFrame(rows)[["pin", "label"] + cols]
    df.attrs["imputation_rates"] = {}
    return df


def test_fit_logistic_regression_picks_real_signal_over_noise():
    df = _build_separable_training_df()
    results = analyze.fit_logistic_regression(df, n_bootstrap=100, random_state=0)
    by_signal = {r["signal"]: r for r in results}

    # lot_size_sf is the dominant predictor and must be significant with a
    # positive coefficient.
    assert by_signal["lot_size_sf"]["significant"] is True
    assert by_signal["lot_size_sf"]["coef"] > 0

    # Pure-noise continuous columns must NOT be significant.
    assert by_signal["cta_distance_ft"]["significant"] is False

    # Normalization fields must be set per-kind.
    assert by_signal["lot_size_sf"]["normalization_min"] is not None
    assert by_signal["lot_size_sf"]["normalization_max"] is not None
    assert by_signal["is_llc"]["normalization_min"] == 0
    assert by_signal["is_llc"]["normalization_max"] == 1


def test_fit_logistic_regression_handles_zero_positives():
    df = _build_separable_training_df(n_pos=0, n_neg=20)
    results = analyze.fit_logistic_regression(df, n_bootstrap=10, random_state=0)
    # No positives → no model can be fit. Return one row per signal with
    # coef=0, significant=False so downstream code doesn't branch.
    assert len(results) == len(analyze.SIGNALS)
    for r in results:
        assert r["coef"] == 0.0
        assert r["significant"] is False


def test_derive_weights_normalizes_significant_only():
    results = [
        # Significant positive, large coef
        {"signal": "lot_size_sf", "kind": "continuous",
         "coef": 0.8, "ci_low": 0.5, "ci_high": 1.1, "significant": True,
         "normalization_min": 1500.0, "normalization_max": 12000.0},
        # Significant negative
        {"signal": "cta_distance_ft", "kind": "continuous",
         "coef": -0.4, "ci_low": -0.7, "ci_high": -0.1, "significant": True,
         "normalization_min": 200.0, "normalization_max": 5000.0},
        # Insignificant — gets weight 0
        {"signal": "is_llc", "kind": "binary",
         "coef": 0.05, "ci_low": -0.2, "ci_high": 0.3, "significant": False,
         "normalization_min": 0.0, "normalization_max": 1.0},
    ]
    weights = analyze.derive_weights(results)
    by_signal = {w["signal"]: w for w in weights}

    # Lot size has 2× the magnitude of cta_distance → 0.8 / 1.2 ≈ 0.667
    assert by_signal["lot_size_sf"]["weight"] == round(0.8 / 1.2, 4)
    assert by_signal["lot_size_sf"]["direction"] == "positive"

    assert by_signal["cta_distance_ft"]["weight"] == round(0.4 / 1.2, 4)
    assert by_signal["cta_distance_ft"]["direction"] == "negative"

    assert by_signal["is_llc"]["weight"] == 0.0
    assert by_signal["is_llc"]["insignificant"] is True

    # Significant weights must sum to 1.0 (modulo rounding).
    sig_sum = sum(w["weight"] for w in weights if not w["insignificant"])
    assert abs(sig_sum - 1.0) < 1e-3


def test_derive_weights_all_insignificant_returns_zero_weights():
    results = [
        {"signal": "lot_size_sf", "kind": "continuous",
         "coef": 0.05, "ci_low": -0.1, "ci_high": 0.2, "significant": False,
         "normalization_min": 0.0, "normalization_max": 1.0},
    ]
    weights = analyze.derive_weights(results)
    assert weights[0]["weight"] == 0.0
    assert weights[0]["insignificant"] is True


def test_write_scoring_yaml_roundtrip(tmp_path):
    weights = [
        {"signal": "lot_size_sf", "kind": "continuous",
         "weight": 0.6, "direction": "positive",
         "normalization": {"min": 1500.0, "max": 12000.0},
         "insignificant": False,
         "coef": 0.8, "ci_low": 0.5, "ci_high": 1.1},
        {"signal": "is_llc", "kind": "binary",
         "weight": 0.0, "direction": "positive",
         "normalization": {"min": 0.0, "max": 1.0},
         "insignificant": True,
         "coef": 0.05, "ci_low": -0.2, "ci_high": 0.3},
    ]
    out_path = tmp_path / "scoring.yaml"
    analyze.write_scoring_yaml(weights, version="1.0.0-test", top_n=20,
                               path=out_path)
    loaded = yaml.safe_load(out_path.read_text())
    assert loaded["version"] == "1.0.0-test"
    assert loaded["top_n"] == 20
    assert "generated_at" in loaded  # ISO-8601 string
    assert set(loaded["signals"].keys()) == {"lot_size_sf", "is_llc"}
    lot = loaded["signals"]["lot_size_sf"]
    assert lot["weight"] == 0.6
    assert lot["direction"] == "positive"
    assert lot["kind"] == "continuous"
    assert lot["normalization"] == {"min": 1500.0, "max": 12000.0}
    assert lot["insignificant"] is False
    assert loaded["signals"]["is_llc"]["insignificant"] is True


def test_write_analysis_report_contains_required_sections(tmp_path):
    funnel = {"total_parcels": 67677, "after_exempt_drop": 67000,
              "after_no_zone_drop": 66800, "after_pd_drop": 64781,
              "after_condo_unit_drop": 17753}
    distributions = [
        {"signal": "lot_size_sf", "kind": "continuous",
         "n_positive": 120, "n_negative": 17633,
         "positive_mean": 9500.0, "negative_mean": 4200.0,
         "positive_median": 8800.0, "negative_median": 3500.0,
         "positive_std": 4000.0, "negative_std": 2200.0},
        {"signal": "is_llc", "kind": "binary",
         "n_positive": 120, "n_negative": 17633,
         "positive_rate": 0.65, "negative_rate": 0.14},
    ]
    weights = [
        {"signal": "lot_size_sf", "kind": "continuous",
         "weight": 0.6, "direction": "positive",
         "normalization": {"min": 1500.0, "max": 12000.0},
         "insignificant": False,
         "coef": 0.8, "ci_low": 0.5, "ci_high": 1.1},
        {"signal": "is_llc", "kind": "binary",
         "weight": 0.4, "direction": "positive",
         "normalization": {"min": 0.0, "max": 1.0},
         "insignificant": False,
         "coef": 0.5, "ci_low": 0.2, "ci_high": 0.8},
    ]
    imputation = {"lot_size_sf": {"n_imputed": 0, "pct": 0.0},
                  "is_llc":      {"n_imputed": 0, "pct": 0.0}}
    out_path = tmp_path / "report.md"
    analyze.write_analysis_report(
        path=out_path,
        db_path=Path("data/full.db"),
        geo_name="Lincoln Park / Lakeview",
        n_positive=120,
        funnel=funnel,
        imputation=imputation,
        distributions=distributions,
        weights=weights,
        version="1.0.0-test",
    )
    body = out_path.read_text()
    # Header
    assert "# Initial Scoring Weights" in body
    assert "Lincoln Park / Lakeview" in body
    assert "data/full.db" in body
    assert "1.0.0-test" in body
    # Funnel mentions every step
    assert "67,677" in body
    assert "17,753" in body
    # Distribution table — at least the column headers and a row
    assert "lot_size_sf" in body
    assert "is_llc" in body
    # Regression results
    assert "0.5" in body and "1.1" in body  # CI bounds
    # Top-5 section
    assert "Top 5 signals by weight" in body
    # Caveats
    assert "Caveats" in body
    assert "tax_delinquent" in body  # the missing signal must be called out
    assert "snapshot" in body.lower()  # snapshot-fidelity caveat


def test_analyze_end_to_end_writes_yaml_and_report(tmp_path):
    """Smoke test: run the full orchestrator on a tiny synthetic DB and confirm
    both output files are written with the expected top-level shape."""
    parcels = []
    permits = []
    # 5 positives — large lots, LLC owners, longer hold
    for i in range(5):
        pin = f"14210010{i:03d}0000"
        parcels.append(_parcel_row(pin, lot_size_sf=8000.0 + i * 200,
                                   hold_duration_years=20.0,
                                   is_llc=1,
                                   address=f"{100 + i} W FAKE ST",
                                   lat=41.93 + i * 0.0001,
                                   lng=-87.65 + i * 0.0001))
        permits.append({
            "permit_number": f"perm-{i}",
            "permit_type": "PERMIT - NEW CONSTRUCTION",
            "issue_date": "2018-05-12",
            "street_number": str(100 + i), "street_direction": "W",
            "street_name": "FAKE ST",
            "latitude": 41.93 + i * 0.0001, "longitude": -87.65 + i * 0.0001,
        })
    # 25 negatives — smaller lots, mostly individual owners, shorter hold
    for i in range(25):
        pin = f"14210020{i:03d}0000"
        parcels.append(_parcel_row(pin, lot_size_sf=3500.0 + i * 50,
                                   hold_duration_years=4.0,
                                   is_llc=0,
                                   address=f"{200 + i} W OTHER ST",
                                   lat=41.94 + i * 0.0001,
                                   lng=-87.66 + i * 0.0001))
    db_path = _build_analyze_db(tmp_path, parcels, permits)
    geo = type("G", (), {"name": "Test Geography"})()  # duck-typed GeographyConfig
    scoring_yaml = tmp_path / "scoring.yaml"
    report_md = tmp_path / "report.md"

    analyze.analyze(db_path=db_path, geo=geo,
                    scoring_yaml_path=scoring_yaml, report_md_path=report_md)

    # YAML
    assert scoring_yaml.exists()
    loaded = yaml.safe_load(scoring_yaml.read_text())
    assert "version" in loaded
    assert "generated_at" in loaded
    assert "signals" in loaded
    # Every SIGNAL must appear in the YAML, even insignificant ones.
    for col, _kind, _src in analyze.SIGNALS:
        assert col in loaded["signals"]

    # Report
    assert report_md.exists()
    body = report_md.read_text()
    assert "Test Geography" in body
    assert "Initial Scoring Weights" in body
    assert "Caveats" in body


import subprocess
import sys


def test_cli_runs_analyze_against_synthetic_db(tmp_path):
    parcels = [_parcel_row(f"14210010{i:03d}0000",
                            lot_size_sf=4000.0 + i * 500,
                            address=f"{100+i} W FAKE ST",
                            lat=41.93 + i*0.0001, lng=-87.65 + i*0.0001)
               for i in range(8)]
    permits = [{"permit_number": "p1",
                "permit_type": "PERMIT - NEW CONSTRUCTION",
                "issue_date": "2019-01-01",
                "street_number": "100", "street_direction": "W",
                "street_name": "FAKE ST",
                "latitude": 41.93, "longitude": -87.65}]
    db_path = _build_analyze_db(tmp_path, parcels, permits)
    # Minimal config dir with just geography.yaml
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "geography.yaml").write_text("""
name: Test
polygon:
  - [41.92, -87.69]
  - [41.92, -87.62]
  - [41.95, -87.62]
  - [41.95, -87.69]
bbox:
  min_lat: 41.92
  max_lat: 41.95
  min_lng: -87.69
  max_lng: -87.62
""".strip())
    scoring = tmp_path / "scoring.yaml"
    report = tmp_path / "report.md"

    result = subprocess.run([
        sys.executable, "-m", "pipeline.analyze",
        "--db", str(db_path),
        "--config-dir", str(config_dir),
        "--scoring-yaml", str(scoring),
        "--report-md", str(report),
    ], capture_output=True, text=True, cwd=str(Path(__file__).resolve().parent.parent))
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert scoring.exists()
    assert report.exists()


import json as _json


def test_build_training_table_includes_groups_and_drops_their_constituents(tmp_path):
    """A consolidation group becomes one training row. Its constituent PINs
    are DROPPED from the parcel-row training set so each redevelopment event
    contributes exactly once. Constituent PINs are NOT removed from the
    parcels table — this drop is training-only."""
    parcels = [
        _parcel_row("14210010010000", lot_size_sf=3000.0,
                    address="100 W FAKE ST", lat=41.93, lng=-87.65),
        _parcel_row("14210010020000", lot_size_sf=4000.0,
                    address="102 W FAKE ST", lat=41.93, lng=-87.65),
        # Unrelated parcel, kept as a regular parcel row
        _parcel_row("14210010030000", lot_size_sf=5000.0,
                    address="200 W OTHER ST", lat=41.94, lng=-87.66),
    ]
    db_path = _build_analyze_db(tmp_path, parcels)
    # Add a consolidation group whose constituents are PIN 1 and PIN 2
    from pipeline.db import get_connection
    conn = get_connection(db_path)
    try:
        conn.execute("""
            INSERT INTO consolidation_groups
              (group_id, pins, combined_lot_size_sf, combined_building_sf,
               owner_name, detected_date)
            VALUES (1, ?, 7000.0, NULL, 'TEST OWNER', '2026-04-28')
        """, (_json.dumps(["14210010010000", "14210010020000"]),))
        conn.commit()
    finally:
        conn.close()

    # PIN 1 had a qualifying permit; PIN 2 did not.
    positives = {"14210010010000": 2018}
    df = analyze.build_training_table(db_path, positives)

    # PIN_1 and PIN_2 are CONSTITUENTS of group 1 → dropped from parcel rows.
    # PIN_3 stays. Group 1 is added. Total = 1 parcel + 1 group = 2 rows.
    assert len(df) == 2
    pins = df["pin"].tolist()
    assert "14210010010000" not in pins   # constituent dropped
    assert "14210010020000" not in pins   # constituent dropped
    assert "14210010030000" in pins        # not in any group → kept
    assert "group:1" in pins

    by_pin = df.set_index("pin")
    # The group is positive because constituent PIN 1 is in positive_pins
    assert by_pin.loc["group:1", "label"] == 1
    # PIN 3 is negative (no permit, not in any group)
    assert by_pin.loc["14210010030000", "label"] == 0

    # Group's lot_size_sf is the COMBINED value (7000)
    assert by_pin.loc["group:1", "lot_size_sf"] == 7000.0

    # Important: the parcels table itself is unchanged — the constituents
    # still exist there with their original data. This is verified by
    # querying parcels directly:
    conn = get_connection(db_path)
    try:
        n_parcels = conn.execute("SELECT COUNT(*) FROM parcels").fetchone()[0]
    finally:
        conn.close()
    assert n_parcels == 3  # all three parcels still in the table


def test_build_training_table_funnel_records_constituent_drop(tmp_path):
    """The funnel exposes both the constituent-drop count and the
    consolidation-groups-added count so the report can show the math."""
    parcels = [
        _parcel_row("14210010010000"),  # constituent of group 1 → dropped
        _parcel_row("14210010020000"),  # constituent of group 1 → dropped
        _parcel_row("14210010030000"),  # standalone → kept
    ]
    db_path = _build_analyze_db(tmp_path, parcels)
    from pipeline.db import get_connection
    conn = get_connection(db_path)
    try:
        conn.execute("""
            INSERT INTO consolidation_groups
              (group_id, pins, combined_lot_size_sf, combined_building_sf,
               owner_name, detected_date)
            VALUES (1, ?, 7000.0, NULL, 'TEST OWNER', '2026-04-28')
        """, (_json.dumps(["14210010010000", "14210010020000"]),))
        conn.commit()
    finally:
        conn.close()

    df = analyze.build_training_table(db_path, positive_pins={})
    funnel = df.attrs["funnel"]
    assert funnel["after_condo_unit_drop"] == 3
    assert funnel["after_constituent_drop"] == 1  # 2 of 3 dropped (PIN_1, PIN_2)
    assert funnel["consolidation_groups_added"] == 1
    assert funnel["after_consolidation_group_add"] == 2  # 1 parcel + 1 group
