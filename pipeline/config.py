from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Any
import yaml


CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


@dataclass(frozen=True)
class GeographyConfig:
    name: str
    polygon: list[tuple[float, float]]   # list of (lat, lng) vertices
    bbox: tuple[float, float, float, float]   # (min_lat, max_lat, min_lng, max_lng)


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with path.open() as f:
        return yaml.safe_load(f) or {}


def get_geography(config_dir: Path = CONFIG_DIR) -> GeographyConfig:
    raw = load_config(config_dir / "geography.yaml")
    polygon = [tuple(pt) for pt in raw["polygon"]]
    b = raw["bbox"]
    return GeographyConfig(
        name=raw["name"],
        polygon=polygon,
        bbox=(b["min_lat"], b["max_lat"], b["min_lng"], b["max_lng"]),
    )
