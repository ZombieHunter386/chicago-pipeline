"""Property tax estimation for Cook County / Chicago parcels.

Cook County tax math:
    EAV          = assessed_total × state_equalizer
    taxable_eav  = max(0, EAV − exemption_eav_reduction)
    annual_tax   = taxable_eav × (composite_rate_pct / 100)

The composite_rate_pct is a city-wide residential average; per-parcel rates
vary by tax code (school/park/library/etc. district combination) and can be
refined later by joining the Cook County Clerk's tax-rate-by-tax-code dataset.
For now we use a single configurable rate, hence "estimated".
"""
from __future__ import annotations
from pathlib import Path

import yaml


REQUIRED_KEYS = ("equalizer", "composite_rate_pct", "homeowner_exemption_eav_reduction")


def load_tax_constants(path: Path) -> dict:
    """Load and validate tax_constants.yaml. Raises KeyError if a required
    key is missing — fail-loud is preferable to silently using zeros."""
    data = yaml.safe_load(path.read_text()) or {}
    for key in REQUIRED_KEYS:
        if key not in data:
            raise KeyError(f"{path} missing required key: {key}")
    return data


def estimate_annual_tax(
    assessed_total: float | None,
    equalizer: float,
    composite_rate_pct: float,
    homeowner_exemption_eav_reduction: float,
    has_homeowner_exemption: bool,
) -> float | None:
    """Return the estimated annual property-tax bill in dollars, or None if
    assessed_total is missing or non-positive."""
    if not assessed_total or assessed_total <= 0:
        return None
    eav = assessed_total * equalizer
    if has_homeowner_exemption:
        eav -= homeowner_exemption_eav_reduction
    if eav < 0:
        eav = 0
    return round(eav * (composite_rate_pct / 100), 2)
