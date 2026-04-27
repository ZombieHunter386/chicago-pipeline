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
