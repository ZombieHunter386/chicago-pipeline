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

import yaml


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


def score_parcel(parcel_row: dict, scoring_config: ScoringConfig) -> float:
    """Filled in by Task 4."""
    raise NotImplementedError("Implemented in Task 4")


def score_parcels(db_path: Path, scoring_config: ScoringConfig) -> int:
    """Filled in by Task 5."""
    raise NotImplementedError("Implemented in Task 5")


def score(db_path: Path, scoring_yaml_path: Path) -> None:
    """Orchestrator — filled in by Task 6."""
    raise NotImplementedError("Implemented in Task 6")
