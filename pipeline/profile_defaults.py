"""Profile defaults registry — binds profile name → scoring YAML →
score column → recommended UI filters.

Source of truth: config/profile_defaults.yaml. Loaded by:
  - pipeline/fetch_all.py (to know which YAMLs to score)
  - webapp routes (to serve recommended filters via /api/profile-defaults)
"""
from __future__ import annotations
from pathlib import Path
from typing import Any

import yaml


REQUIRED_FIELDS = ("yaml", "score_column")


def load_profile_defaults(path: Path) -> dict[str, dict[str, Any]]:
    """Load the registry. Returns dict keyed by profile_name with:
      - yaml: relative path to the scoring YAML
      - score_column: column in parcels to write
      - recommended_filters: dict of filter defaults (auto-applied in UI)

    Raises KeyError if a profile entry is missing required fields.
    """
    with Path(path).open() as f:
        raw = yaml.safe_load(f) or {}
    for profile_name, body in raw.items():
        for field in REQUIRED_FIELDS:
            if field not in body:
                raise KeyError(
                    f"profile_defaults.yaml: profile {profile_name!r} "
                    f"missing required field {field!r}"
                )
        body.setdefault("recommended_filters", {})
    return raw
