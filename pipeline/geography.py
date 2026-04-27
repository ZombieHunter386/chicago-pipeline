# pipeline/geography.py
from __future__ import annotations
from typing import Iterable
from shapely.geometry import Point, Polygon
from pipeline.config import GeographyConfig


def _polygon(geo: GeographyConfig) -> Polygon:
    # GeographyConfig.polygon is list of (lat, lng); shapely wants (x=lng, y=lat)
    return Polygon([(lng, lat) for lat, lng in geo.polygon])


def in_polygon(lat: float, lng: float, geo: GeographyConfig) -> bool:
    if lat is None or lng is None:
        return False
    poly = _polygon(geo)
    return poly.covers(Point(lng, lat))


def filter_by_polygon(
    rows: Iterable[dict],
    geo: GeographyConfig,
    lat_field: str = "lat",
    lng_field: str = "lng",
) -> list[dict]:
    poly = _polygon(geo)
    out = []
    for r in rows:
        lat = r.get(lat_field)
        lng = r.get(lng_field)
        if lat is None or lng is None:
            continue
        try:
            if poly.covers(Point(float(lng), float(lat))):
                out.append(r)
        except (TypeError, ValueError):
            continue
    return out


def bbox_where_clause(
    geo: GeographyConfig,
    lat_field: str = "lat",
    lng_field: str = "lon",
) -> str:
    """SoQL $where clause for a coarse bounding-box prefilter."""
    min_lat, max_lat, min_lng, max_lng = geo.bbox
    return (
        f"{lat_field} between {min_lat} and {max_lat} "
        f"AND {lng_field} between {min_lng} and {max_lng}"
    )


def bbox_polygon_wkt(geo: GeographyConfig) -> str:
    """WKT polygon describing geo.bbox, suitable for Socrata intersects()."""
    min_lat, max_lat, min_lng, max_lng = geo.bbox
    return (
        f"POLYGON(({min_lng} {min_lat},{max_lng} {min_lat},"
        f"{max_lng} {max_lat},{min_lng} {max_lat},{min_lng} {min_lat}))"
    )


def geom_intersects_clause(
    geo: GeographyConfig, geom_field: str = "the_geom"
) -> str:
    """SoQL $where filtering rows whose geometry intersects the geo bbox.
    Used for datasets keyed on a polygon column rather than lat/lng pair."""
    return f"intersects({geom_field}, '{bbox_polygon_wkt(geo)}')"
