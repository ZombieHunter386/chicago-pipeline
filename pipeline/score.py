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
