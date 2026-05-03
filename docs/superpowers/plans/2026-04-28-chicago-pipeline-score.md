# Chicago Pipeline — Score Step Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Score step of the chicago-pipeline. Read `config/scoring.yaml` (produced by the Analyze step), walk the `parcels` table, compute a 0–100 score per parcel using a deterministic weighted-sum formula, write `score` + `score_version` per row. One-off run against `data/full.db` produces the column the Review UI already filters on.

**Architecture:** A new module `pipeline/score.py` with five public-ish functions (`load_scoring_config`, `normalize_signal`, `score_parcel`, `score_parcels`, `score`) and one CLI. Pure functions where possible — `normalize_signal` and `score_parcel` are unit-testable without a DB; `score_parcels` is the only function that touches SQLite. Scoring is fully deterministic given the same DB + same YAML; running it twice produces identical scores. No new dependencies (uses pyyaml already in `requirements.txt`).

**Tech stack:** Python 3.14, SQLite, pyyaml. Working dir: `/Users/hunterheyman/Claude/chicago-pipeline`. Run pytest as `.venv/bin/pytest`.

**Verification baseline:** before starting, run `.venv/bin/pytest -q` from `chicago-pipeline/` — must show **183 passing**. After every task, all tests must still pass. Final task runs the module against `data/full.db` to populate the live `score` column.

---

## Design decisions (locked in upfront)

These are the ambiguous bits that the Score plan inherits from the Analyze plan's "Score Plan Scope" section. Decided before any code lands so subsequent tasks don't re-litigate.

### 1. Direction handling: **flip the normalized value**

For `direction: negative` signals (e.g. `estimated_annual_tax`, `hold_duration_years`), the per-signal contribution is:

```python
contribution = (1.0 - normalized) * weight    # negative direction
contribution = normalized * weight             # positive direction
```

Where `normalized ∈ [0, 1]`. Equivalent algebraically to negating the weight, but this form keeps the per-signal contribution non-negative, which makes the UI's score-breakdown component (already shipped) read cleanly. "Lot size contributed 0.12 out of its 0.15 weight" makes more sense than "lot size contributed −0.03 out of its −0.15 weight".

### 2. Aggregation: **fixed scale, `Σ(contribution) × 100`**

Significant weights in the YAML sum to 1.0 (Task 7 of the Analyze plan guaranteed this). Each contribution is in `[0, weight]`, so the raw sum is in `[0, 1]`, and the final score is in `[0, 100]`.

NOT renormalizing per-run to `max_observed = 100`. Two reasons:
- Reproducible across runs and across DB snapshots.
- A score of 90 means "top 10% of the THEORETICALLY achievable score," which is a meaningful global anchor — not "top 10% of THIS run," which drifts whenever the DB changes.

The trade-off: in practice no parcel will hit 100 because no parcel is simultaneously at the 95th percentile of every positive signal AND the 5th percentile of every negative signal. The realistic distribution will compress into roughly `[10, 60]` with a long upper tail. That's fine for ranking; the Review UI sorts by score, and "85" being the practical ceiling is a feature, not a bug.

### 3. NULL handling at score time: **0.5 (neutral)**

The `parcels` table has NULLs for many signals. The Analyze step imputed with training-set medians **in-memory only** — those imputations were never written back. So at score time, NULL-on-parcel is the common case.

For a NULL raw value, `normalize_signal` returns **0.5** (the neutral midpoint of the `[0, 1]` normalized scale). This is approximately equivalent to imputing the median because the YAML's `normalization.min`/`max` is the 5th–95th percentile, so the median sits near 0.5 in normalized space.

Why not propagate the actual median through the YAML? Two reasons: (1) it would require extending the YAML schema and the Analyze writer, which is out of scope; (2) 0.5 is close enough for v1 and the report's caveats already disclose the imputation rates.

### 4. `min == max` edge case: **return 0.5**

Five signals on `data/full.db` have degenerate normalization ranges because they're 91–100% imputed during Analyze. They all carry `insignificant: true` and `weight: 0`, so they contribute zero regardless. But `normalize_signal` should not crash — it should return 0.5 when `min == max`.

### 5. Out-of-range raw values: **clip**

If a parcel's `lot_size_sf` is above the 95th percentile cap (`normalization.max`), normalize to 1.0. Below the 5th percentile cap, normalize to 0.0. We do NOT extrapolate — the linear fit in the regression doesn't hold beyond the training range.

### 6. Insignificant signals: **fully zeroed out**

`insignificant: true` signals have `weight: 0` and contribute 0 regardless. Score iterates over them anyway (so the breakdown component can show "this signal didn't fire because it was insignificant"). Zero contribution math doesn't need a special branch.

### 7. Score persistence and idempotency

`UPDATE parcels SET score = ?, score_version = ? WHERE pin = ?` per parcel. Always overwrite — no skipping. Two runs with the same DB + same YAML must produce byte-identical scores. We don't bump `last_updated_date` from a Score run because it isn't a fetch.

### 8. Consolidation-group scoring: **deferred to a future plan**

The master spec calls for scoring `consolidation_groups` as a single entity using their combined fields. v1 of Score handles the `parcels` table only. Reasons:
- Most signals on consolidation_groups would need per-signal aggregation rules (SUM tax, MAX max_far, MIN cta_distance_ft, etc.) — that's a separate design exercise.
- The Review UI already lists consolidation groups as a separate map layer; v1 of Score can leave their `score` column NULL and the UI shows them via constituent-parcel max-score (or a placeholder).
- Shipping parcels-only Score unblocks the Review UI's score column TODAY. Consolidation-group scoring is its own clean increment that can ship later.

This decision is reversible — adding consolidation-group scoring later is a strict superset, not a refactor of v1.

---

## File structure

- **Create:** `pipeline/score.py` — new module, ~150-200 lines.
- **Create:** `tests/test_pipeline_score.py` — new test file.
- **Modify:** `data/full.db` (parcels.score + parcels.score_version columns populated).
- Schema is unchanged (`score` and `score_version` columns already exist in `pipeline/db.py`).

---

## Phase 0 — Scaffolding

### Task 1: Scaffold `pipeline/score.py` + the public API stub

**Files:**
- Create: `pipeline/score.py`
- Create: `tests/test_pipeline_score.py`

This task lays down the module skeleton: typed config dataclasses, the `score()` entry-point signature, and a single test asserting the public surface. Subsequent tasks fill in the logic.

- [ ] **Step 1: Write the failing test**

Create `tests/test_pipeline_score.py`:

```python
"""Tests for pipeline/score.py — applies weights from config/scoring.yaml
to the parcels table to produce a 0-100 score per parcel."""
from pipeline import score


def test_module_exposes_expected_public_api():
    """score, normalize_signal, score_parcel, score_parcels, load_scoring_config
    are the public functions. They get filled in across Tasks 2-6."""
    for name in ("score", "normalize_signal", "score_parcel", "score_parcels",
                 "load_scoring_config"):
        assert hasattr(score, name), f"pipeline.score missing {name}"


def test_score_entry_point_signature():
    import inspect
    sig = inspect.signature(score.score)
    assert list(sig.parameters) == ["db_path", "scoring_yaml_path"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/pytest tests/test_pipeline_score.py -v`

Expected: FAIL with `ImportError: cannot import name 'score' from 'pipeline'` (since the module doesn't exist yet).

- [ ] **Step 3: Create the stub module**

Create `pipeline/score.py`:

```python
"""Score step — apply weights from config/scoring.yaml to the parcels table.

Reads the YAML produced by pipeline.analyze, walks every parcel in the
`parcels` table, computes a 0-100 score using a deterministic weighted-sum
formula, and writes `score` + `score_version` per row.

Pure-function design: normalize_signal and score_parcel are testable without
a DB; score_parcels is the only function that touches SQLite.
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SignalConfig:
    """Per-signal configuration loaded from scoring.yaml."""
    signal: str
    kind: str               # "continuous" | "binary"
    weight: float
    direction: str          # "positive" | "negative"
    normalization_min: float
    normalization_max: float
    insignificant: bool


@dataclass(frozen=True)
class ScoringConfig:
    """Top-level config loaded from scoring.yaml."""
    version: str
    top_n: int
    signals: list[SignalConfig]   # iteration order matches YAML order


def load_scoring_config(path: Path) -> ScoringConfig:
    """Filled in by Task 2."""
    raise NotImplementedError("Implemented in Task 2")


def normalize_signal(raw_value: float | int | None,
                     signal_config: SignalConfig) -> float:
    """Filled in by Task 3."""
    raise NotImplementedError("Implemented in Task 3")


def score_parcel(parcel_row: dict, scoring_config: ScoringConfig) -> float:
    """Filled in by Task 4."""
    raise NotImplementedError("Implemented in Task 4")


def score_parcels(db_path: Path, scoring_config: ScoringConfig) -> int:
    """Filled in by Task 5."""
    raise NotImplementedError("Implemented in Task 5")


def score(db_path: Path, scoring_yaml_path: Path) -> None:
    """Orchestrator — filled in by Task 6."""
    raise NotImplementedError("Implemented in Task 6")
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/pytest tests/test_pipeline_score.py -v`

Expected: 2 tests PASS.

- [ ] **Step 5: Confirm full suite still passes**

Run: `.venv/bin/pytest -q`

Expected: **185 passing** (was 183, +2 new).

- [ ] **Step 6: Commit**

```bash
git add pipeline/score.py tests/test_pipeline_score.py
git commit -m "feat(score): scaffold pipeline/score.py + dataclasses

Public surface only — every function raises NotImplementedError. Subsequent
tasks fill in load_scoring_config, normalize_signal, score_parcel,
score_parcels, and the score() orchestrator."
```

---

## Phase 1 — Config loader

### Task 2: `load_scoring_config(path)` — typed config from the YAML

**Files:**
- Modify: `pipeline/score.py`
- Modify: `tests/test_pipeline_score.py`

The YAML format is locked by Analyze (Task 8 of the Analyze plan):

```yaml
version: "1.0.0-2026-04-28"
generated_at: "2026-04-28T..."
top_n: 20
signals:
  lot_size_sf:
    kind: continuous
    weight: 0.0
    direction: positive
    normalization: { min: 1500.0, max: 12000.0 }
    insignificant: true
  is_llc:
    kind: binary
    weight: 0.153
    direction: positive
    normalization: { min: 0.0, max: 1.0 }
    insignificant: false
  # ... 15 more
```

The loader returns a `ScoringConfig` with `signals` as a list (preserving YAML insertion order) so iteration is deterministic.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_pipeline_score.py`:

```python
import yaml


def _write_yaml(path, payload):
    path.write_text(yaml.safe_dump(payload, sort_keys=False))


def test_load_scoring_config_basic_roundtrip(tmp_path):
    yaml_path = tmp_path / "scoring.yaml"
    _write_yaml(yaml_path, {
        "version": "1.0.0-test",
        "generated_at": "2026-04-28T12:00:00+00:00",
        "top_n": 20,
        "signals": {
            "lot_size_sf": {"kind": "continuous", "weight": 0.0,
                            "direction": "positive",
                            "normalization": {"min": 1500.0, "max": 12000.0},
                            "insignificant": True},
            "is_llc":      {"kind": "binary", "weight": 0.153,
                            "direction": "positive",
                            "normalization": {"min": 0.0, "max": 1.0},
                            "insignificant": False},
        },
    })
    cfg = score.load_scoring_config(yaml_path)
    assert cfg.version == "1.0.0-test"
    assert cfg.top_n == 20
    assert len(cfg.signals) == 2
    # Order preserved.
    assert [s.signal for s in cfg.signals] == ["lot_size_sf", "is_llc"]
    lot = cfg.signals[0]
    assert lot.kind == "continuous"
    assert lot.weight == 0.0
    assert lot.normalization_min == 1500.0
    assert lot.normalization_max == 12000.0
    assert lot.insignificant is True
    llc = cfg.signals[1]
    assert llc.weight == 0.153
    assert llc.insignificant is False


def test_load_scoring_config_raises_on_missing_required_field(tmp_path):
    yaml_path = tmp_path / "scoring.yaml"
    _write_yaml(yaml_path, {
        "version": "1.0.0-test",
        "top_n": 20,
        # signals key missing entirely
    })
    import pytest
    with pytest.raises(KeyError, match="signals"):
        score.load_scoring_config(yaml_path)
```

- [ ] **Step 2: Run the failing test**

Run: `.venv/bin/pytest tests/test_pipeline_score.py::test_load_scoring_config_basic_roundtrip -v`

Expected: FAIL with `NotImplementedError: Implemented in Task 2`.

- [ ] **Step 3: Implement `load_scoring_config`**

Replace the stub in `pipeline/score.py`. Add at the top with other imports:

```python
import yaml
```

…and replace the function:

```python
def load_scoring_config(path: Path) -> ScoringConfig:
    """Load and validate config/scoring.yaml. Returns a ScoringConfig with
    signals as a list (preserving YAML insertion order). Raises KeyError if
    a required field is missing — fail-loud is preferable to silently using
    defaults."""
    with path.open() as f:
        raw = yaml.safe_load(f)
    if "signals" not in raw:
        raise KeyError(f"{path} missing required field: signals")
    signals = []
    for name, body in raw["signals"].items():
        signals.append(SignalConfig(
            signal=name,
            kind=body["kind"],
            weight=float(body["weight"]),
            direction=body["direction"],
            normalization_min=float(body["normalization"]["min"]),
            normalization_max=float(body["normalization"]["max"]),
            insignificant=bool(body["insignificant"]),
        ))
    return ScoringConfig(
        version=raw["version"],
        top_n=int(raw.get("top_n", 20)),
        signals=signals,
    )
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/test_pipeline_score.py -v`

Expected: 4 tests PASS.

- [ ] **Step 5: Full suite**

Run: `.venv/bin/pytest -q`

Expected: **187 passing**.

- [ ] **Step 6: Commit**

```bash
git add pipeline/score.py tests/test_pipeline_score.py
git commit -m "feat(score): load_scoring_config — typed config from scoring.yaml

YAML insertion order is preserved as a list (so iteration in score_parcel
matches the Analyze SIGNALS order). Raises KeyError on missing signals
field; fail-loud is preferable to silently scoring with defaults."
```

---

## Phase 2 — Per-signal normalization

### Task 3: `normalize_signal(raw_value, signal_config)` — return float in [0, 1]

**Files:**
- Modify: `pipeline/score.py`
- Modify: `tests/test_pipeline_score.py`

This is the load-bearing function. Six edge cases the tests pin down:

1. Continuous, in-range: linear rescale `(raw - min) / (max - min)`.
2. Continuous, above max: clip to 1.0.
3. Continuous, below min: clip to 0.0.
4. Continuous, NULL: return 0.5 (neutral).
5. Continuous, `min == max`: return 0.5 (degenerate range).
6. Binary, value present: cast to float (0.0 or 1.0).
7. Binary, NULL: return 0.0 (binary NULL means "not flagged").

NOTE: Direction is NOT applied here. `normalize_signal` returns the raw normalized value in `[0, 1]`. Direction handling is the responsibility of `score_parcel` (Task 4) so the breakdown can show "raw normalized = 0.85, direction negative, contribution = (1 - 0.85) × weight".

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pipeline_score.py`:

```python
def _continuous_cfg(min_=0.0, max_=100.0, insignificant=False):
    return score.SignalConfig(
        signal="test", kind="continuous", weight=0.5, direction="positive",
        normalization_min=min_, normalization_max=max_,
        insignificant=insignificant,
    )


def _binary_cfg(insignificant=False):
    return score.SignalConfig(
        signal="test", kind="binary", weight=0.5, direction="positive",
        normalization_min=0.0, normalization_max=1.0,
        insignificant=insignificant,
    )


def test_normalize_continuous_in_range():
    cfg = _continuous_cfg(min_=1500.0, max_=12000.0)
    # Halfway between min and max → 0.5
    assert score.normalize_signal(6750.0, cfg) == 0.5
    # Quarter point
    assert score.normalize_signal(4125.0, cfg) == 0.25


def test_normalize_continuous_clips_above_max():
    cfg = _continuous_cfg(min_=1500.0, max_=12000.0)
    assert score.normalize_signal(50000.0, cfg) == 1.0


def test_normalize_continuous_clips_below_min():
    cfg = _continuous_cfg(min_=1500.0, max_=12000.0)
    assert score.normalize_signal(100.0, cfg) == 0.0


def test_normalize_continuous_null_returns_neutral():
    cfg = _continuous_cfg(min_=1500.0, max_=12000.0)
    assert score.normalize_signal(None, cfg) == 0.5


def test_normalize_continuous_degenerate_range_returns_neutral():
    """When min == max (signal was 100% imputed during Analyze), return 0.5
    instead of dividing by zero. These signals are insignificant anyway, so
    contribution will be zero — but normalize must not crash."""
    cfg = _continuous_cfg(min_=7.62, max_=7.62, insignificant=True)
    assert score.normalize_signal(7.62, cfg) == 0.5
    assert score.normalize_signal(0.0, cfg) == 0.5
    assert score.normalize_signal(None, cfg) == 0.5


def test_normalize_binary_value():
    cfg = _binary_cfg()
    assert score.normalize_signal(1, cfg) == 1.0
    assert score.normalize_signal(0, cfg) == 0.0


def test_normalize_binary_null_returns_zero():
    """Binary NULL means 'not flagged' — contributes 0, not 0.5 (which
    would inflate the score for unflagged parcels)."""
    cfg = _binary_cfg()
    assert score.normalize_signal(None, cfg) == 0.0
```

- [ ] **Step 2: Run the failing tests**

Run: `.venv/bin/pytest tests/test_pipeline_score.py::test_normalize_continuous_in_range -v`

Expected: FAIL with `NotImplementedError: Implemented in Task 3`.

- [ ] **Step 3: Implement `normalize_signal`**

Replace the stub in `pipeline/score.py`:

```python
def normalize_signal(raw_value: float | int | None,
                     signal_config: SignalConfig) -> float:
    """Return the raw value normalized to [0, 1] for the given signal.

    Continuous: linear rescale by the (min, max) range, clipped at the bounds.
    Binary: cast to float (0.0 or 1.0).
    NULL: 0.5 for continuous (neutral), 0.0 for binary (not-flagged).
    Degenerate range (min == max): 0.5 (avoids divide-by-zero on insignificant
        signals that were 100% imputed during Analyze).

    Direction is NOT applied here — score_parcel handles the flip.
    """
    if signal_config.kind == "binary":
        if raw_value is None:
            return 0.0
        return float(bool(raw_value))

    # Continuous
    if raw_value is None:
        return 0.5
    lo = signal_config.normalization_min
    hi = signal_config.normalization_max
    if hi == lo:
        return 0.5
    if raw_value <= lo:
        return 0.0
    if raw_value >= hi:
        return 1.0
    return (float(raw_value) - lo) / (hi - lo)
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/test_pipeline_score.py -v`

Expected: 11 tests PASS.

- [ ] **Step 5: Full suite**

Run: `.venv/bin/pytest -q`

Expected: **194 passing**.

- [ ] **Step 6: Commit**

```bash
git add pipeline/score.py tests/test_pipeline_score.py
git commit -m "feat(score): normalize_signal handles continuous, binary, NULL, and degenerate range

Continuous: linear rescale clipped to [0, 1]. Binary: cast to float. NULL:
0.5 for continuous (neutral midpoint ≈ training median), 0.0 for binary
(not-flagged). min == max: 0.5 (avoids divide-by-zero on insignificant
signals that were 100% imputed during Analyze). Direction handling lives
in score_parcel."
```

---

## Phase 3 — Per-parcel scoring

### Task 4: `score_parcel(parcel_row, scoring_config)` — float in [0, 100]

**Files:**
- Modify: `pipeline/score.py`
- Modify: `tests/test_pipeline_score.py`

Walks the signals in YAML order. For each:
- `normalized = normalize_signal(parcel_row[signal_name], signal_config)`
- If `direction == "negative"`: `contribution = (1 - normalized) * weight`
- Else: `contribution = normalized * weight`
- Sum all contributions, multiply by 100.

Insignificant signals have `weight: 0` so contribute 0 regardless. No special branch needed.

The result is `[0, 100]` because the sum of significant weights is 1.0 and each contribution is in `[0, weight]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pipeline_score.py`:

```python
def _config(signals):
    return score.ScoringConfig(version="1.0.0-test", top_n=20, signals=signals)


def test_score_parcel_positive_direction_in_range():
    """A parcel halfway through the lot_size range with weight 1.0
    (only signal) should score 50."""
    cfg = _config([
        score.SignalConfig(signal="lot_size_sf", kind="continuous", weight=1.0,
                           direction="positive",
                           normalization_min=1000.0, normalization_max=11000.0,
                           insignificant=False),
    ])
    parcel = {"lot_size_sf": 6000.0}  # exactly the midpoint
    assert score.score_parcel(parcel, cfg) == 50.0


def test_score_parcel_negative_direction_flips():
    """Negative-direction signal at the high end of its range should
    contribute LESS to the score than a parcel at the low end."""
    cfg = _config([
        score.SignalConfig(signal="estimated_annual_tax", kind="continuous",
                           weight=1.0, direction="negative",
                           normalization_min=1000.0, normalization_max=11000.0,
                           insignificant=False),
    ])
    high_tax = {"estimated_annual_tax": 11000.0}   # normalized = 1.0
    low_tax  = {"estimated_annual_tax": 1000.0}    # normalized = 0.0
    # High tax → flipped to 0.0 → contribution 0.0 → score 0
    assert score.score_parcel(high_tax, cfg) == 0.0
    # Low tax → flipped to 1.0 → contribution 1.0 → score 100
    assert score.score_parcel(low_tax, cfg) == 100.0


def test_score_parcel_combines_weighted_signals():
    """Weights sum to 1.0 across signals; per-signal contribution is in
    [0, weight]; final score is in [0, 100]."""
    cfg = _config([
        score.SignalConfig(signal="lot_size_sf", kind="continuous", weight=0.6,
                           direction="positive",
                           normalization_min=0.0, normalization_max=10000.0,
                           insignificant=False),
        score.SignalConfig(signal="is_llc", kind="binary", weight=0.4,
                           direction="positive",
                           normalization_min=0.0, normalization_max=1.0,
                           insignificant=False),
    ])
    # Parcel at 75% of lot range (normalized 0.75) and is_llc=1
    # contributions: 0.75 * 0.6 + 1.0 * 0.4 = 0.45 + 0.40 = 0.85 → 85
    parcel = {"lot_size_sf": 7500.0, "is_llc": 1}
    assert score.score_parcel(parcel, cfg) == 85.0


def test_score_parcel_insignificant_signal_contributes_zero():
    """Even with a non-zero raw value, weight=0 means contribution=0."""
    cfg = _config([
        score.SignalConfig(signal="lot_size_sf", kind="continuous", weight=1.0,
                           direction="positive",
                           normalization_min=0.0, normalization_max=10000.0,
                           insignificant=False),
        score.SignalConfig(signal="years_since_last_permit", kind="continuous",
                           weight=0.0, direction="positive",
                           normalization_min=7.62, normalization_max=7.62,
                           insignificant=True),
    ])
    parcel = {"lot_size_sf": 5000.0, "years_since_last_permit": 999.0}
    # Only lot_size contributes: 0.5 * 1.0 = 0.5 → 50
    assert score.score_parcel(parcel, cfg) == 50.0


def test_score_parcel_handles_null_columns():
    """Missing signal in parcel_row treated as NULL → 0.5 for continuous,
    0 for binary."""
    cfg = _config([
        score.SignalConfig(signal="lot_size_sf", kind="continuous", weight=0.5,
                           direction="positive",
                           normalization_min=0.0, normalization_max=10000.0,
                           insignificant=False),
        score.SignalConfig(signal="is_llc", kind="binary", weight=0.5,
                           direction="positive",
                           normalization_min=0.0, normalization_max=1.0,
                           insignificant=False),
    ])
    parcel = {"lot_size_sf": None, "is_llc": None}
    # lot_size NULL → 0.5; is_llc NULL → 0.0
    # contributions: 0.5 * 0.5 + 0.0 * 0.5 = 0.25 → 25
    assert score.score_parcel(parcel, cfg) == 25.0


def test_score_parcel_clamps_to_zero_to_hundred():
    """Sanity check: every realistic input lands in [0, 100]."""
    cfg = _config([
        score.SignalConfig(signal="x", kind="continuous", weight=1.0,
                           direction="positive",
                           normalization_min=0.0, normalization_max=100.0,
                           insignificant=False),
    ])
    assert score.score_parcel({"x": -5000}, cfg) == 0.0
    assert score.score_parcel({"x": 5000}, cfg) == 100.0
```

- [ ] **Step 2: Run the failing tests**

Run: `.venv/bin/pytest tests/test_pipeline_score.py::test_score_parcel_positive_direction_in_range -v`

Expected: FAIL with `NotImplementedError: Implemented in Task 4`.

- [ ] **Step 3: Implement `score_parcel`**

Replace the stub in `pipeline/score.py`:

```python
def score_parcel(parcel_row: dict, scoring_config: ScoringConfig) -> float:
    """Compute a 0-100 score for a single parcel.

    For each signal in YAML order:
      - normalize the parcel's raw value to [0, 1]
      - flip if direction is negative: contribution = (1 - normalized) * weight
      - else:                          contribution = normalized * weight
      - add to running total

    Multiply by 100 to scale into [0, 100]. Significant weights sum to 1.0
    so the score is bounded to that range.

    Insignificant signals have weight=0 — they contribute 0 by arithmetic.
    """
    total = 0.0
    for sig in scoring_config.signals:
        raw = parcel_row.get(sig.signal)
        normalized = normalize_signal(raw, sig)
        if sig.direction == "negative":
            contribution = (1.0 - normalized) * sig.weight
        else:
            contribution = normalized * sig.weight
        total += contribution
    return round(total * 100, 4)
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/test_pipeline_score.py -v`

Expected: 17 tests PASS.

- [ ] **Step 5: Full suite**

Run: `.venv/bin/pytest -q`

Expected: **200 passing**.

- [ ] **Step 6: Commit**

```bash
git add pipeline/score.py tests/test_pipeline_score.py
git commit -m "feat(score): score_parcel — weighted sum of normalized signals → [0, 100]

Walks signals in YAML order. Per-signal contribution = (flipped if negative)
× weight. Sum × 100. Insignificant signals contribute 0 by arithmetic
(weight=0). Result rounded to 4 decimals; deterministic given the same
parcel + same config."
```

---

## Phase 4 — Bulk DB scoring

### Task 5: `score_parcels(db_path, scoring_config)` — UPDATE all parcels

**Files:**
- Modify: `pipeline/score.py`
- Modify: `tests/test_pipeline_score.py`

Reads every parcel, calls `score_parcel`, writes `score` + `score_version` per row in a single transaction. Returns the count of rows updated.

Implementation note: SELECT only the columns we need (the signal columns + pin), not `SELECT *`. The `parcels` table has 70+ columns and we only consume the 17 signals.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_pipeline_score.py`:

```python
from datetime import datetime, UTC
from pipeline.db import init_db, upsert_rows


def _build_score_db(tmp_path, parcels):
    db_path = tmp_path / "score.db"
    init_db(db_path)
    now = datetime.now(UTC).isoformat(timespec="seconds")
    upsert_rows(db_path, "parcels",
                [{**p, "last_fetched_date": now} for p in parcels],
                key_columns=["pin"])
    return db_path


def test_score_parcels_updates_score_and_version(tmp_path):
    parcels = [
        {"pin": "14210010010000", "lot_size_sf": 5000.0, "is_llc": 1},
        {"pin": "14210010020000", "lot_size_sf": 9000.0, "is_llc": 0},
    ]
    db_path = _build_score_db(tmp_path, parcels)
    cfg = _config([
        score.SignalConfig(signal="lot_size_sf", kind="continuous", weight=0.5,
                           direction="positive",
                           normalization_min=0.0, normalization_max=10000.0,
                           insignificant=False),
        score.SignalConfig(signal="is_llc", kind="binary", weight=0.5,
                           direction="positive",
                           normalization_min=0.0, normalization_max=1.0,
                           insignificant=False),
    ])
    cfg = score.ScoringConfig(version="1.0.0-test", top_n=20, signals=cfg.signals)
    n = score.score_parcels(db_path, cfg)
    assert n == 2

    from pipeline.db import get_connection
    conn = get_connection(db_path)
    try:
        rows = {r["pin"]: dict(r) for r in conn.execute(
            "SELECT pin, score, score_version FROM parcels"
        ).fetchall()}
    finally:
        conn.close()

    # Parcel 1: lot=0.5*0.5 + is_llc=1.0*0.5 = 0.75 → 75
    assert rows["14210010010000"]["score"] == 75.0
    assert rows["14210010010000"]["score_version"] == "1.0.0-test"
    # Parcel 2: lot=0.9*0.5 + is_llc=0.0*0.5 = 0.45 → 45
    assert rows["14210010020000"]["score"] == 45.0
    assert rows["14210010020000"]["score_version"] == "1.0.0-test"


def test_score_parcels_is_idempotent(tmp_path):
    """Running score_parcels twice with the same DB + config produces identical
    score values — Score is deterministic, no per-run randomness."""
    parcels = [{"pin": "14210010010000", "lot_size_sf": 5000.0, "is_llc": 1}]
    db_path = _build_score_db(tmp_path, parcels)
    cfg = score.ScoringConfig(
        version="1.0.0-test", top_n=20,
        signals=[score.SignalConfig(signal="lot_size_sf", kind="continuous",
                                    weight=1.0, direction="positive",
                                    normalization_min=0.0, normalization_max=10000.0,
                                    insignificant=False)],
    )
    score.score_parcels(db_path, cfg)
    from pipeline.db import get_connection
    conn = get_connection(db_path)
    try:
        first = conn.execute(
            "SELECT score FROM parcels WHERE pin='14210010010000'"
        ).fetchone()["score"]
    finally:
        conn.close()

    score.score_parcels(db_path, cfg)
    conn = get_connection(db_path)
    try:
        second = conn.execute(
            "SELECT score FROM parcels WHERE pin='14210010010000'"
        ).fetchone()["score"]
    finally:
        conn.close()

    assert first == second


def test_score_parcels_handles_empty_db(tmp_path):
    """No parcels → returns 0, no-op."""
    db_path = tmp_path / "empty.db"
    init_db(db_path)
    cfg = score.ScoringConfig(version="1.0.0-test", top_n=20, signals=[])
    assert score.score_parcels(db_path, cfg) == 0
```

- [ ] **Step 2: Run the failing test**

Run: `.venv/bin/pytest tests/test_pipeline_score.py::test_score_parcels_updates_score_and_version -v`

Expected: FAIL with `NotImplementedError: Implemented in Task 5`.

- [ ] **Step 3: Implement `score_parcels`**

Add to `pipeline/score.py`:

```python
from pipeline.db import get_connection


def score_parcels(db_path: Path, scoring_config: ScoringConfig) -> int:
    """Score every parcel in the DB; UPDATE score + score_version per row.

    Reads only the columns the config references plus `pin`. Always overwrites
    existing scores. Returns the count of rows updated.
    """
    if not scoring_config.signals:
        return 0
    feature_cols = [s.signal for s in scoring_config.signals]
    select_cols = ["pin"] + feature_cols
    select_sql = "SELECT " + ", ".join(select_cols) + " FROM parcels"

    conn = get_connection(db_path)
    try:
        rows = [dict(r) for r in conn.execute(select_sql).fetchall()]
        if not rows:
            return 0
        updates = [
            {"pin": r["pin"],
             "score": score_parcel(r, scoring_config),
             "score_version": scoring_config.version}
            for r in rows
        ]
        conn.executemany(
            "UPDATE parcels SET score = :score, score_version = :score_version "
            "WHERE pin = :pin",
            updates,
        )
        conn.commit()
        return len(updates)
    finally:
        conn.close()
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/test_pipeline_score.py -v`

Expected: 20 tests PASS.

- [ ] **Step 5: Full suite**

Run: `.venv/bin/pytest -q`

Expected: **203 passing**.

- [ ] **Step 6: Commit**

```bash
git add pipeline/score.py tests/test_pipeline_score.py
git commit -m "feat(score): score_parcels — bulk UPDATE in a single transaction

SELECT pin + only the signal columns the config references (avoids reading
70+ unused columns). Calls score_parcel per row. Idempotent — two runs
with the same DB + config produce identical scores."
```

---

## Phase 5 — Orchestrator + CLI

### Task 6: `score(db_path, scoring_yaml_path)` + `python -m pipeline.score`

**Files:**
- Modify: `pipeline/score.py`
- Modify: `tests/test_pipeline_score.py`

Tiny orchestrator (load config + score parcels) plus an argparse CLI. Mirrors `pipeline/analyze.py`'s structure.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pipeline_score.py`:

```python
def test_score_orchestrator_writes_scores(tmp_path):
    parcels = [{"pin": "14210010010000", "lot_size_sf": 5000.0}]
    db_path = _build_score_db(tmp_path, parcels)
    yaml_path = tmp_path / "scoring.yaml"
    _write_yaml(yaml_path, {
        "version": "1.0.0-test",
        "generated_at": "2026-04-28T12:00:00+00:00",
        "top_n": 20,
        "signals": {
            "lot_size_sf": {"kind": "continuous", "weight": 1.0,
                            "direction": "positive",
                            "normalization": {"min": 0.0, "max": 10000.0},
                            "insignificant": False},
        },
    })
    score.score(db_path=db_path, scoring_yaml_path=yaml_path)

    from pipeline.db import get_connection
    conn = get_connection(db_path)
    try:
        row = dict(conn.execute(
            "SELECT score, score_version FROM parcels WHERE pin='14210010010000'"
        ).fetchone())
    finally:
        conn.close()
    # 5000 / 10000 * 100 = 50
    assert row["score"] == 50.0
    assert row["score_version"] == "1.0.0-test"


def test_cli_runs_score_against_synthetic_db(tmp_path):
    import subprocess, sys
    parcels = [{"pin": "14210010010000", "lot_size_sf": 5000.0}]
    db_path = _build_score_db(tmp_path, parcels)
    yaml_path = tmp_path / "scoring.yaml"
    _write_yaml(yaml_path, {
        "version": "1.0.0-cli-test",
        "generated_at": "2026-04-28T12:00:00+00:00",
        "top_n": 20,
        "signals": {
            "lot_size_sf": {"kind": "continuous", "weight": 1.0,
                            "direction": "positive",
                            "normalization": {"min": 0.0, "max": 10000.0},
                            "insignificant": False},
        },
    })
    result = subprocess.run([
        sys.executable, "-m", "pipeline.score",
        "--db", str(db_path),
        "--scoring-yaml", str(yaml_path),
    ], capture_output=True, text=True,
       cwd=str(Path(__file__).resolve().parent.parent))
    assert result.returncode == 0, f"stderr: {result.stderr}"
    from pipeline.db import get_connection
    conn = get_connection(db_path)
    try:
        version = conn.execute(
            "SELECT score_version FROM parcels WHERE pin='14210010010000'"
        ).fetchone()["score_version"]
    finally:
        conn.close()
    assert version == "1.0.0-cli-test"
```

- [ ] **Step 2: Run the failing tests**

Run: `.venv/bin/pytest tests/test_pipeline_score.py::test_score_orchestrator_writes_scores -v`

Expected: FAIL with `NotImplementedError: Implemented in Task 6`.

- [ ] **Step 3: Implement orchestrator + CLI**

Replace the stub `score()` in `pipeline/score.py`:

```python
def score(db_path: Path, scoring_yaml_path: Path) -> None:
    """Orchestrate: load config + score every parcel in the DB."""
    cfg = load_scoring_config(scoring_yaml_path)
    n = score_parcels(db_path, cfg)
    print(f"Scored {n:,} parcels with version {cfg.version}")


def _cli(argv: list[str] | None = None) -> int:
    import argparse
    p = argparse.ArgumentParser(prog="pipeline.score",
                                description="Apply scoring weights to every parcel.")
    p.add_argument("--db", required=True, type=Path,
                   help="Path to the SQLite DB (e.g. data/full.db).")
    p.add_argument("--scoring-yaml", required=True, type=Path,
                   help="Path to config/scoring.yaml.")
    args = p.parse_args(argv)
    score(db_path=args.db, scoring_yaml_path=args.scoring_yaml)
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/test_pipeline_score.py -v`

Expected: 22 tests PASS.

- [ ] **Step 5: Full suite**

Run: `.venv/bin/pytest -q`

Expected: **205 passing**.

- [ ] **Step 6: Commit**

```bash
git add pipeline/score.py tests/test_pipeline_score.py
git commit -m "feat(score): orchestrator + CLI

score() loads the YAML and scores every parcel. python -m pipeline.score
takes --db and --scoring-yaml. Mirrors pipeline.analyze's CLI shape so the
two steps are usable as siblings from the shell."
```

---

## Phase 6 — Run against the live DB

### Task 7: Run against `data/full.db`, sanity-check, capture summary

**Files:** None (reads + writes `data/full.db`).

This is the one-shot run that populates the live `score` column. No code changes — just CLI invocation + verification.

- [ ] **Step 1: Confirm prerequisites**

Run: `cd /Users/hunterheyman/Claude/chicago-pipeline && ls -lh data/full.db config/scoring.yaml`

Expected: both files exist. `data/full.db` ~628 MB. `config/scoring.yaml` was produced by Task 12 of the Analyze plan (commit `73a752d`).

Run: `.venv/bin/python -c "import sqlite3; c=sqlite3.connect('data/full.db'); n=c.execute('SELECT COUNT(*) FROM parcels').fetchone()[0]; print(f'parcels: {n}'); s=c.execute('SELECT COUNT(*) FROM parcels WHERE score IS NOT NULL').fetchone()[0]; print(f'already scored: {s}')"`

Expected: 67,677 parcels; 0 already scored (this is the first scoring run).

- [ ] **Step 2: Run Score**

```bash
cd /Users/hunterheyman/Claude/chicago-pipeline
.venv/bin/python -m pipeline.score --db data/full.db --scoring-yaml config/scoring.yaml
```

Expected: prints `Scored 67,677 parcels with version 1.0.0-2026-04-28` (or the version stamp from the actual scoring.yaml). Wall time should be under 30 seconds — pure CPU, no API calls, no bootstrap.

- [ ] **Step 3: Sanity-check the score distribution**

```bash
.venv/bin/python -c "
import sqlite3
c = sqlite3.connect('data/full.db')
c.row_factory = sqlite3.Row
n_total = c.execute('SELECT COUNT(*) FROM parcels').fetchone()[0]
n_scored = c.execute('SELECT COUNT(*) FROM parcels WHERE score IS NOT NULL').fetchone()[0]
print(f'parcels total: {n_total:,}')
print(f'parcels scored: {n_scored:,}')
stats = c.execute('SELECT MIN(score), MAX(score), AVG(score), COUNT(DISTINCT score_version) FROM parcels WHERE score IS NOT NULL').fetchone()
print(f'score min={stats[0]:.2f} max={stats[1]:.2f} mean={stats[2]:.2f} versions={stats[3]}')
quintiles = c.execute('SELECT score FROM parcels WHERE score IS NOT NULL ORDER BY score').fetchall()
n=len(quintiles)
print(f'p10={quintiles[n//10][\"score\"]:.2f} p50={quintiles[n//2][\"score\"]:.2f} p90={quintiles[n*9//10][\"score\"]:.2f} p99={quintiles[n*99//100][\"score\"]:.2f}')
top5 = c.execute('SELECT pin, address, score FROM parcels ORDER BY score DESC LIMIT 5').fetchall()
print('top 5:')
for r in top5:
    print(f'  {r[\"pin\"]} | {r[\"address\"]} | {r[\"score\"]:.2f}')
"
```

Expected:
- `n_scored == n_total` (every parcel scored, no NULLs).
- `score min ≥ 0`, `score max ≤ 100` (tighter range likely — the theoretical max isn't reachable in practice).
- Exactly 1 distinct `score_version`.
- Distribution is non-degenerate: p10 noticeably below p90, no all-zero or all-50 collapse.
- Top 5 parcels look plausible — likely small-tax, short-hold, LLC-owned non-multifamily-zoned parcels (matching the regression's signal directions).

- [ ] **Step 4: Reproducibility check**

```bash
.venv/bin/python -c "
import sqlite3
c = sqlite3.connect('data/full.db')
sample = [r[0] for r in c.execute('SELECT score FROM parcels ORDER BY pin LIMIT 100').fetchall()]
print('first 100 by pin:', sample[:5], '...')
"
```

Re-run Score:

```bash
.venv/bin/python -m pipeline.score --db data/full.db --scoring-yaml config/scoring.yaml
```

Re-run the sample check. Expected: identical first-100 sample. Score is deterministic.

- [ ] **Step 5: Commit metadata only**

The scoring run wrote into `data/full.db`, which is gitignored (it's a 628 MB binary blob). Nothing to commit from Step 2 itself. But document the run in a brief file:

Actually — `data/full.db` IS in the repo's working tree but NOT tracked in git (check: `git status` shouldn't list it). Verify:

```bash
cd /Users/hunterheyman/Claude/chicago-pipeline && git status --short data/
```

Expected: `data/full.db` is not listed (untracked or gitignored). If it IS listed, do NOT add it — that's a 628 MB blob.

If `git status` shows ONLY `data/fetch_full.log` or similar log files (untracked, expected) and no `data/full.db` line → no commit needed for this task. Skip to Step 6.

- [ ] **Step 6: Report final summary**

Per the user's "what done looks like" criteria for the Score step:

1. **Number of parcels scored** — should be 67,677 (every parcel in the DB).
2. **Score distribution** — min/max/mean/p10/p50/p90/p99 from Step 3.
3. **Top 5 parcels by score** — addresses + scores.
4. **Sanity observations** — does the top-5 list match the regression's narrative (cheap parcels, short holds, LLC owners, non-multifamily-by-right zoning)? If not, that's a signal of a bug.

---

## Self-review

Walk back through the spec one more time:

- [x] **Scoring is a weighted sum (continuous normalized to 0–1, binary as 0/1):** Tasks 3-4. Continuous via linear-rescale-and-clip, binary via float cast.
- [x] **Initial weights from `config/scoring.yaml`:** Task 2 loads it.
- [x] **`score_version` recorded per parcel:** Task 5.
- [x] **Direction handling:** decision §1, implementation Task 4. Flip the normalized value for negative direction.
- [x] **Aggregation:** decision §2, fixed scale `Σ(contribution) × 100`.
- [x] **NULL handling:** decision §3, 0.5 for continuous, 0.0 for binary.
- [x] **`min == max` edge case:** decision §4, 0.5. Tested in Task 3.
- [x] **Out-of-range clipping:** decision §5, clip to bounds. Tested in Task 3.
- [x] **Insignificant signals contribute 0:** decision §6, falls out of arithmetic. Tested in Task 4.
- [x] **Idempotency:** decision §7. Tested in Task 5.
- [x] **CLI:** Task 6.
- [x] **Real-DB run:** Task 7.
- [x] **Don't break tests:** every task ends with `.venv/bin/pytest -q` checkpoint. Test count grows from 183 → 205 (+22 new).
- [x] **TDD:** every code-bearing task is test-first.
- [x] **Fixture-driven tests:** every Phase 0-5 test uses an inline `_build_score_db` helper or in-memory dicts. Only Task 7 touches `data/full.db`.

Out of scope, by design (decision §8):
- Consolidation-group scoring (`consolidation_groups` table). Defer to a follow-up plan.

No placeholders, every code block self-contained.

---

## Future plans (after this lands)

1. **Consolidation-group scoring** — the next clean increment. Score the `consolidation_groups` table using combined fields, with documented per-signal aggregation rules (SUM tax, MAX max_far, MIN cta_distance_ft, etc.). Will need a new module method `score_consolidation_groups(db_path, config)` and decisions about how to derive non-additive fields (zoning class, owner attributes — they're shared by definition for consolidation groups). Estimated 3-5 tasks.

2. **Surface scores in the Review UI** — the UI's score-breakdown component already exists from Plan 2 (the data-foundation plan). Once `parcels.score` is populated, the UI's left-panel sort and right-panel breakdown light up automatically. No code changes likely needed; verify by running the dev server against the freshly-scored DB.

3. **Feedback loop** — after a couple of outreach waves, response data accumulates and you can adjust weights manually in `config/scoring.yaml`. Re-running `python -m pipeline.score` produces a new `score_version` and updated scores. The Feedback Report UI section already exists from the master spec; wire it up once you have wave data.
