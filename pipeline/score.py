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

from pipeline.consolidation_features import derive_group_features
from pipeline.db import get_connection


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


def score_parcels(db_path: Path, scoring_config: ScoringConfig) -> int:
    """Score every eligible parcel in the DB; UPDATE score + score_version per row.

    Always clears stale scores from ALL parcels first, then writes fresh scores
    only for rows passing the eligibility filter (currently: is_condo_unit=0).
    This ensures methodology changes that exclude previously-scored populations
    don't leave stale scores in place. Returns the count of rows scored.
    """
    if not scoring_config.signals:
        return 0
    feature_cols = [s.signal for s in scoring_config.signals]
    select_cols = ["pin"] + feature_cols
    select_sql = ("SELECT " + ", ".join(select_cols) + " FROM parcels "
                  "WHERE COALESCE(is_condo_unit, 0) = 0")

    conn = get_connection(db_path)
    try:
        rows = [dict(r) for r in conn.execute(select_sql).fetchall()]
        # Clear stale scores from EVERY parcel up-front so filtered-out rows
        # don't retain values from a prior methodology.
        conn.execute("UPDATE parcels SET score = NULL, score_version = NULL")
        if not rows:
            conn.commit()
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


def score_consolidation_groups(db_path: Path,
                               scoring_config: ScoringConfig) -> int:
    """Score every consolidation group; UPDATE score + score_version per row.

    Each group's features are aggregated from its constituent parcels via
    derive_group_features, then scored through score_parcel using the same
    weights/normalization as parcels. Returns the count of groups updated.
    """
    if not scoring_config.signals:
        return 0
    conn = get_connection(db_path)
    try:
        group_ids = [r["group_id"] for r in conn.execute(
            "SELECT group_id FROM consolidation_groups"
        ).fetchall()]
    finally:
        conn.close()

    # Clear stale scores from every group first so the table state is fully
    # determined by this run's config (mirrors score_parcels' behavior).
    conn = get_connection(db_path)
    try:
        conn.execute("UPDATE consolidation_groups SET score = NULL, "
                     "score_version = NULL")
        conn.commit()
    finally:
        conn.close()

    if not group_ids:
        return 0

    updates = []
    for gid in group_ids:
        features = derive_group_features(gid, db_path)
        updates.append({
            "group_id": gid,
            "score": score_parcel(features, scoring_config),
            "score_version": scoring_config.version,
        })

    conn = get_connection(db_path)
    try:
        conn.executemany(
            "UPDATE consolidation_groups SET score = :score, "
            "score_version = :score_version WHERE group_id = :group_id",
            updates,
        )
        conn.commit()
    finally:
        conn.close()
    return len(updates)


def score(db_path: Path, scoring_yaml_path: Path) -> None:
    """Orchestrate: load config + score every parcel + every consolidation group."""
    cfg = load_scoring_config(scoring_yaml_path)
    n_parcels = score_parcels(db_path, cfg)
    n_groups = score_consolidation_groups(db_path, cfg)
    print(f"Scored {n_parcels:,} parcels and {n_groups:,} consolidation groups "
          f"with version {cfg.version}")


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
