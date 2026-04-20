"""Source 2A — Chicago Zoning Districts (with spatial join)."""
from __future__ import annotations
import json
from datetime import datetime, UTC
from pathlib import Path
import geopandas as gpd
from shapely.geometry import shape, Point
from pipeline.config import GeographyConfig, CONFIG_DIR
from pipeline.db import upsert_rows, get_connection
from pipeline.socrata import SocrataClient
from pipeline.zoning_lookup import load_zoning_lookup


DATASET_ID = "7cve-jgbp"
TABLE = "raw_cdp_zoning"
SOURCE_NAME = "cdp_zoning"


def fetch(geo: GeographyConfig, db_path: Path, client: SocrataClient,
          zoning_lookup_path: Path | None = None) -> int:
    fetched_at = datetime.now(UTC).isoformat(timespec="seconds")
    zoning_lookup_path = zoning_lookup_path or (CONFIG_DIR / "zoning_lookup.yaml")
    zone_info = load_zoning_lookup(zoning_lookup_path)

    raw_rows = []
    polys = []
    for r in client.fetch(DATASET_ID):
        gj = r.get("the_geom")
        if not gj:
            continue
        if isinstance(gj, str):
            gj = json.loads(gj)
        try:
            geom = shape(gj)
        except Exception:
            continue
        zc = r.get("zone_class")
        oid = r.get("objectid") or r.get("zone_id") or f"{zc}-{len(raw_rows)}"
        raw_rows.append({
            "objectid": str(oid),
            "zone_class": zc,
            "geom_geojson": json.dumps(gj),
            "pd_num": r.get("pd_num"),
            "fetched_at": fetched_at,
        })
        polys.append({"objectid": str(oid), "zone_class": zc, "geometry": geom})

    n = upsert_rows(db_path, TABLE, raw_rows, key_columns=["objectid"])

    if not polys:
        return n

    zones_gdf = gpd.GeoDataFrame(polys, crs="EPSG:4326")

    # Build a parcels GeoDataFrame from current DB
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT pin, lat, lng, built_far FROM parcels WHERE lat IS NOT NULL AND lng IS NOT NULL"
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        return n

    points = gpd.GeoDataFrame(
        [{"pin": r["pin"], "built_far": r["built_far"],
          "geometry": Point(r["lng"], r["lat"])} for r in rows],
        crs="EPSG:4326",
    )

    joined = gpd.sjoin(points, zones_gdf, how="left", predicate="within")

    conn = get_connection(db_path)
    try:
        for _, j in joined.iterrows():
            zc = j.get("zone_class")
            zi = zone_info.get(zc) if zc else None
            max_far = zi.max_far if zi else None
            allows_mf = 1 if (zi and zi.allows_multifamily) else 0
            built = j["built_far"]
            far_gap = (max_far / built) if (max_far and built and built > 0) else None
            conn.execute("""
                UPDATE parcels SET
                    zone_class = :zc,
                    max_far = :max_far,
                    far_gap = :far_gap,
                    allows_multifamily_by_right = :amf,
                    last_updated_date = :now
                WHERE pin = :pin
            """, {"zc": zc, "max_far": max_far, "far_gap": far_gap,
                  "amf": allows_mf, "now": fetched_at, "pin": j["pin"]})
        conn.commit()
    finally:
        conn.close()
    return n
