"""Source 1H — Cook County GIS Parcel Boundaries (polygon geometries).

Provides the authoritative `lot_size_sf` for every parcel by computing the
area of the GIS polygon in EPSG:3435 (NAD83 Illinois East, US survey feet →
square feet directly). Replaces the assessor's `char_land_sf` as the source
of truth because the GIS layer covers condos and vacant lots that have no
characteristics record (~40% of parcels at smoke scale otherwise lacked a
lot size).

Polygons are keyed on `pin10` (the 10-digit building / lot code), so every
PIN sharing a `pin10` gets the same lot area — which is correct: condo
units share a lot with each other and with the building rep.

After writing `lot_size_sf`, the source recomputes `built_far` for every
parcel that has a `building_sf` so that derived FAR values pick up the new
lot data. `cdp_zoning` runs later in the orchestrator and re-derives
`max_units_allowed` and `far_gap` from the same lot_size.
"""
from __future__ import annotations
import json
from datetime import datetime, UTC
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import shape, Point

from pipeline.config import GeographyConfig
from pipeline.db import upsert_rows, get_connection
from pipeline.geography import geom_intersects_clause, _polygon
from pipeline.socrata import SocrataClient


DATASET_ID = "77tz-riq7"  # ccgisdata - Parcel 2021 (latest year on Socrata)
TABLE = "raw_ccgis_parcels"
SOURCE_NAME = "ccgis_parcels"

# NAD83 Illinois East (US survey feet) — planar CRS suited to Cook County
# so polygon area comes out in square feet directly.
PLANAR_CRS = "EPSG:3435"


def _polygon_to_width_depth(geom) -> tuple[float, float]:
    """Compute (width, depth) of a polygon in its native CRS units by
    taking the side lengths of its minimum rotated rectangle.

    Width is always the shorter side; depth the longer. For Chicago lots
    in EPSG:3435 (US survey feet) this is street frontage × lot depth.

    For non-rectangular polygons (L-shape, wedge, corner), the minimum
    rotated rectangle is an over-bounding rectangle, so width slightly
    over-states the true narrow dimension. Acceptable for scoring per
    the design spec — ordering of candidates matters, not exact dimensions.
    """
    mbr = geom.minimum_rotated_rectangle
    if mbr.is_empty or mbr.geom_type != "Polygon":
        return (0.0, 0.0)
    coords = list(mbr.exterior.coords)
    if len(coords) < 4:
        return (0.0, 0.0)
    # First three corners give us two adjacent sides.
    p0, p1, p2 = coords[0], coords[1], coords[2]
    side_a = ((p1[0] - p0[0]) ** 2 + (p1[1] - p0[1]) ** 2) ** 0.5
    side_b = ((p2[0] - p1[0]) ** 2 + (p2[1] - p1[1]) ** 2) ** 0.5
    return (min(side_a, side_b), max(side_a, side_b))


def fetch(geo: GeographyConfig, db_path: Path, client: SocrataClient) -> int:
    fetched_at = datetime.now(UTC).isoformat(timespec="seconds")

    where = geom_intersects_clause(geo, geom_field="the_geom")
    polys = []
    for r in client.fetch(DATASET_ID, where=where, select="pin10,the_geom"):
        gj = r.get("the_geom")
        pin10 = r.get("pin10")
        if not gj or not pin10:
            continue
        if isinstance(gj, str):
            gj = json.loads(gj)
        try:
            geom = shape(gj)
        except Exception:
            continue
        polys.append({"pin10": pin10, "geometry": geom})

    if not polys:
        return 0

    # Refine to those whose centroid is inside the polygon (the bbox prefilter
    # admits parcels at the bbox corners that fall outside the actual geo).
    target_poly = _polygon(geo)
    refined = [
        p for p in polys
        if target_poly.covers(Point(p["geometry"].centroid.x, p["geometry"].centroid.y))
    ]
    if not refined:
        return 0

    # Compute area in EPSG:3435 (US survey feet) → area in sq ft.
    gdf = gpd.GeoDataFrame(refined, crs="EPSG:4326").to_crs(PLANAR_CRS)
    gdf["area_sf"] = gdf.geometry.area
    # One pin10 may appear in multiple rows in the source dataset
    # (multipart oddities); keep the largest area per pin10.
    gdf = gdf.sort_values("area_sf", ascending=False).drop_duplicates(subset=["pin10"], keep="first")

    raw_rows = [
        {"pin10": row["pin10"], "area_sf": float(row["area_sf"]),
         "fetched_at": fetched_at}
        for _, row in gdf.iterrows()
    ]
    n = upsert_rows(db_path, TABLE, raw_rows, key_columns=["pin10"])

    conn = get_connection(db_path)
    try:
        # Apply lot_size_sf to every parcel sharing each pin10. Use a single
        # batched UPDATE per pin10 for tractability; at full geography this
        # is up to ~280K UPDATEs but each is a primary-key match via the
        # idx_parcels_pin10 index added in the condo-rollup change.
        for row in raw_rows:
            conn.execute(
                "UPDATE parcels SET lot_size_sf = :lot_sf, last_updated_date = :now "
                "WHERE pin10 = :p10",
                {"lot_sf": row["area_sf"], "now": fetched_at, "p10": row["pin10"]},
            )

        # Recompute built_far for every parcel that has both fields populated.
        # This re-derivation is cheap and idempotent — runs against the new
        # lot_size_sf so any stale value from a prior assessor-derived run
        # gets corrected.
        conn.execute(
            "UPDATE parcels SET "
            "  built_far = ROUND(building_sf * 1.0 / lot_size_sf, 2), "
            "  last_updated_date = ? "
            "WHERE building_sf IS NOT NULL "
            "  AND lot_size_sf IS NOT NULL "
            "  AND lot_size_sf > 0",
            (fetched_at,),
        )
        conn.commit()
    finally:
        conn.close()
    return n
