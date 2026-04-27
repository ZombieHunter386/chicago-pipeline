"""Source 2A — Chicago Zoning Districts (with spatial join)."""
from __future__ import annotations
import json
from datetime import datetime, UTC
from pathlib import Path
import geopandas as gpd
import pandas as pd
from shapely.geometry import shape, Point
from pipeline.config import GeographyConfig, CONFIG_DIR
from pipeline.db import upsert_rows, get_connection
from pipeline.socrata import SocrataClient
from pipeline.zoning_lookup import load_zoning_lookup


# Chicago Data Portal: Boundaries - Zoning Districts (current).
# The "Map" variant (7cve-jgbp) doesn't expose data via the SODA API and
# returned 0 rows silently in the previous smoke run.
DATASET_ID = "dj47-wfun"
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
        raise RuntimeError(
            f"cdp_zoning: dataset {DATASET_ID} returned 0 polygons. "
            f"Verify the dataset id is current and the Socrata endpoint is reachable."
        )

    zones_gdf = gpd.GeoDataFrame(polys, crs="EPSG:4326")

    # Build a parcels GeoDataFrame from current DB
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT pin, lat, lng, built_far, lot_size_sf FROM parcels "
            "WHERE lat IS NOT NULL AND lng IS NOT NULL"
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        return n

    points = gpd.GeoDataFrame(
        [{"pin": r["pin"], "built_far": r["built_far"],
          "lot_size_sf": r["lot_size_sf"],
          "geometry": Point(r["lng"], r["lat"])} for r in rows],
        crs="EPSG:4326",
    )

    joined = gpd.sjoin(points, zones_gdf, how="left", predicate="within")
    # Dedup to one row per pin — overlapping zones (rare: layered PDs) would
    # otherwise cause non-deterministic last-write-wins UPDATEs.
    joined = joined.drop_duplicates(subset=["pin"], keep="first")

    conn = get_connection(db_path)
    try:
        for _, j in joined.iterrows():
            zc_raw = j.get("zone_class")
            # Left sjoin yields NaN (float) for parcels outside any polygon.
            # Coerce to None so it doesn't serialize as the string "nan" into TEXT.
            zc = None if zc_raw is None or pd.isna(zc_raw) else zc_raw
            zi = zone_info.get(zc) if zc else None
            max_far = zi.max_far if zi else None
            # When zone is unknown, leave allows_multifamily_by_right as NULL
            # rather than asserting 0 ("does not allow"). Only write a value
            # when we have a real zone lookup.
            allows_mf = None if zi is None else (1 if zi.allows_multifamily else 0)
            built = j["built_far"]
            far_gap = (max_far / built) if (max_far and built and built > 0) else None
            min_lot_pu = zi.min_lot_area_per_unit if zi else None
            lot_raw = j["lot_size_sf"]
            lot = None if lot_raw is None or pd.isna(lot_raw) else float(lot_raw)
            max_units = (
                int(lot // min_lot_pu)
                if (lot and min_lot_pu and min_lot_pu > 0)
                else None
            )
            conn.execute("""
                UPDATE parcels SET
                    zone_class = :zc,
                    max_far = :max_far,
                    far_gap = :far_gap,
                    allows_multifamily_by_right = :amf,
                    min_lot_area_per_unit = :mlpu,
                    max_units_allowed = :mu,
                    last_updated_date = :now
                WHERE pin = :pin
            """, {"zc": zc, "max_far": max_far, "far_gap": far_gap,
                  "amf": allows_mf, "mlpu": min_lot_pu, "mu": max_units,
                  "now": fetched_at, "pin": j["pin"]})
        conn.commit()
    finally:
        conn.close()
    return n
