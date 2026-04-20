from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import yaml


@dataclass(frozen=True)
class ZoneInfo:
    max_far: Optional[float]
    max_height_ft: Optional[float]
    max_density: Optional[float]
    min_lot_area_per_unit: Optional[float]
    setback_front_ft: Optional[float]
    setback_side_ft: Optional[float]
    setback_rear_ft: Optional[float]
    allows_multifamily: bool


def load_zoning_lookup(path: Path) -> dict[str, ZoneInfo]:
    with path.open() as f:
        data = yaml.safe_load(f) or {}
    out: dict[str, ZoneInfo] = {}
    for zone, attrs in (data.get("zones") or {}).items():
        out[zone] = ZoneInfo(
            max_far=attrs.get("max_far"),
            max_height_ft=attrs.get("max_height_ft"),
            max_density=attrs.get("max_density"),
            min_lot_area_per_unit=attrs.get("min_lot_area_per_unit"),
            setback_front_ft=attrs.get("setback_front_ft"),
            setback_side_ft=attrs.get("setback_side_ft"),
            setback_rear_ft=attrs.get("setback_rear_ft"),
            allows_multifamily=bool(attrs.get("allows_multifamily")),
        )
    return out
