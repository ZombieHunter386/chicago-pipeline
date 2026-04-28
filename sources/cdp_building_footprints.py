"""Source 2I — Chicago Building Footprints (syp8-uezg).

WARNING: This dataset is frozen. The Socrata "Last updated" timestamp on
the portal is a metadata touch, not a row refresh; max(year_built) across
the dataset is 2010-2011 and max(edit_date) is 2015-02-27. We use it as a
stale-but-useful BACKSTOP for parcels where the Cook County Assessor's
Improvement Characteristics dataset has no value (mainly condo buildings
and 5xx/3xx commercial classes that the chars dataset doesn't cover).

Merge rule (uniform across year_built, building_sf, unit_count, condition):

    if assessor has a value:    keep assessor (always wins when present)
    elif footprint has a value: use footprint (translated for condition)
    else:                       NULL

Spot-checking on Lincoln Ave / Diversey / Wolfram parcels in 2026-04-27
showed the footprint dataset systematically under-reports SF on multi-story
buildings (likely because story counts in syp8-uezg are stale or wrong),
so we no longer prefer footprint even for pre-2015 buildings. Footprint
keeps its role for filling NULLs where assessor has nothing.

`building_sf_source` and `condition_source` get tagged with the winner
('assessor' or 'footprint') so the provenance is visible per-row.

For multi-footprint parcels (28.7% of matches) we take the LARGEST-AREA
structure for all four fields — main building over garage/coach house.

Condo handling: a single building's footprint spatially contains the
centroids of every unit PIN (they share lat/lng), so the polygon-in-point
join naturally hits all of them. We **redirect** any matched is_condo_unit
PIN to its building's rep PIN (the one with is_condo_building=1 sharing
the same pin10), then dedupe — so building_sf gets written exactly once
per pin10 and the condo rollup's later sum doesn't double-count.

Order requirement: this fetcher must run AFTER pipeline.condo_rollup so
the is_condo_building flag is current. fetch_all.py wires it accordingly.
"""
from __future__ import annotations
import json
from collections import defaultdict
from datetime import datetime, UTC
from pathlib import Path

import geopandas as gpd
from shapely.geometry import shape, Point

from pipeline.config import GeographyConfig
from pipeline.db import upsert_rows, get_connection
from pipeline.geography import (
    filter_by_polygon,
    bbox_where_clause,
)
from pipeline.socrata import SocrataClient
from pipeline.spatial import PLANAR_CRS


DATASET_ID = "syp8-uezg"
TABLE = "raw_cdp_building_footprints"
SOURCE_NAME = "cdp_building_footprints"

# Translate footprints' bldg_condi (4-tier) to the assessor's vocabulary,
# extending it with "Poor" and "Uninhabitable" so the underlying severity
# of NEEDS MAJOR REPAIR / UNINHABITABLE is preserved.
CONDITION_MAP = {
    "SOUND": "Average",
    "NEEDS MINOR REPAIR": "Below Average",
    "NEEDS MAJOR REPAIR": "Poor",
    "UNINHABITABLE": "Uninhabitable",
}


def _f(v):
    if v in (None, ""): return None
    try: return float(v)
    except (TypeError, ValueError): return None


def _i(v):
    if v in (None, ""): return None
    try: return int(float(v))
    except (TypeError, ValueError): return None


def _pos_i(v):
    """Like _i but treats 0 as 'not populated'. The footprints dataset uses
    0 as the sentinel for empty year_built / no_of_unit / no_stories."""
    out = _i(v)
    return None if (out is None or out == 0) else out


def _pos_f(v):
    """Like _f but treats 0.0 as 'not populated'. The footprints dataset
    uses 0.0 as the sentinel for empty bldg_sq_fo."""
    out = _f(v)
    return None if (out is None or out == 0.0) else out


def _translate_condition(bldg_condi: str | None) -> str | None:
    if not bldg_condi:
        return None
    return CONDITION_MAP.get(bldg_condi.strip().upper())


def fetch(geo: GeographyConfig, db_path: Path, client: SocrataClient) -> int:
    fetched_at = datetime.now(UTC).isoformat(timespec="seconds")

    # The dataset uses MultiPolygon geometry, not lat/lng. The bbox prefilter
    # uses within_box on the_geom; final clip happens in Python via the
    # polygon centroid.
    min_lat, max_lat, min_lng, max_lng = geo.bbox
    where = (
        f"within_box(the_geom, {max_lat}, {min_lng}, {min_lat}, {max_lng})"
    )

    raw_rows: list[dict] = []
    for r in client.fetch(DATASET_ID, where=where):
        gj = r.get("the_geom")
        if not gj:
            continue
        if isinstance(gj, str):
            try:
                gj = json.loads(gj)
            except (TypeError, ValueError):
                continue
        try:
            geom = shape(gj)
            centroid = geom.centroid
        except Exception:
            continue

        bldg_id = r.get("bldg_id")
        if not bldg_id:
            continue

        raw_rows.append({
            "bldg_id": str(bldg_id),
            "bldg_statu": r.get("bldg_statu"),
            "f_add1": r.get("f_add1"),
            "t_add1": r.get("t_add1"),
            "pre_dir1": r.get("pre_dir1"),
            "st_name1": r.get("st_name1"),
            "st_type1": r.get("st_type1"),
            "suf_dir1": r.get("suf_dir1"),
            "bldg_sq_fo": _pos_f(r.get("bldg_sq_fo")),
            "shape_area": _f(r.get("shape_area")),
            "stories": _pos_i(r.get("stories")),
            "no_of_unit": _pos_i(r.get("no_of_unit")),
            "year_built": _pos_i(r.get("year_built")),
            "bldg_condi": r.get("bldg_condi"),
            "demolished": (r.get("demolished") or "")[:10] or None,
            "edit_date": (r.get("edit_date") or "")[:10] or None,
            "geom_geojson": json.dumps(gj),
            "centroid_lat": float(centroid.y) if not centroid.is_empty else None,
            "centroid_lng": float(centroid.x) if not centroid.is_empty else None,
            "_geom": geom,
            "fetched_at": fetched_at,
        })

    # Polygon clip to target geography using each footprint's centroid.
    raw_rows = filter_by_polygon(
        raw_rows, geo,
        lat_field="centroid_lat", lng_field="centroid_lng",
    )

    # ACTIVE-only per project rule: drop demolished/inactive structures.
    raw_rows = [
        r for r in raw_rows
        if r["bldg_statu"] == "ACTIVE" and not r.get("demolished")
    ]

    # Persist raw (without _geom field which isn't a column).
    persistable = [{k: v for k, v in r.items() if k != "_geom"} for r in raw_rows]
    n = upsert_rows(db_path, TABLE, persistable, key_columns=["bldg_id"])

    if not raw_rows:
        return n

    # ---- Spatial join: footprint polygon -> parcel centroid (point in polygon).
    # Each footprint may cover multiple parcel PINs (shared building); each
    # parcel may be covered by multiple footprints (main + garage). Build a
    # PIN -> [matched footprint] map.
    conn = get_connection(db_path)
    try:
        parcels = [dict(p) for p in conn.execute(
            "SELECT pin, pin10, lat, lng, year_built, building_sf, "
            "       condition, unit_count, "
            "       lot_size_sf, max_far, "
            "       COALESCE(is_condo_unit, 0) AS is_condo_unit, "
            "       COALESCE(is_condo_building, 0) AS is_condo_building "
            "FROM parcels WHERE lat IS NOT NULL AND lng IS NOT NULL"
        ).fetchall()]
    finally:
        conn.close()

    # pin10 -> rep PIN (for condos this is is_condo_building=1; for non-condos
    # it's just the parcel's own PIN, since there's a 1:1 mapping).
    rep_for_pin10: dict[str, str] = {}
    for p in parcels:
        if p["is_condo_building"]:
            rep_for_pin10[p["pin10"]] = p["pin"]
    # For non-condo pin10s, fall back to the parcel itself.
    for p in parcels:
        if p["pin10"] not in rep_for_pin10 and not p["is_condo_unit"]:
            rep_for_pin10.setdefault(p["pin10"], p["pin"])

    parcel_gdf = gpd.GeoDataFrame(
        [{"pin": p["pin"], "pin10": p["pin10"],
          "is_condo_unit": p["is_condo_unit"],
          "geometry": Point(p["lng"], p["lat"])} for p in parcels],
        crs="EPSG:4326",
    ).to_crs(PLANAR_CRS)

    fp_gdf = gpd.GeoDataFrame(
        [{"_idx": i, "geometry": r["_geom"]} for i, r in enumerate(raw_rows)],
        crs="EPSG:4326",
    ).to_crs(PLANAR_CRS)

    joined = gpd.sjoin(parcel_gdf, fp_gdf, how="inner", predicate="within")

    # Redirect each match through the rep PIN (so condo unit PINs that all
    # share a building's footprint resolve to the single rep), then collect
    # footprint indices per rep so we can pick the largest structure.
    rep_to_idxs: dict[str, set[int]] = defaultdict(set)
    for _, row in joined.iterrows():
        pin = row["pin"]
        pin10 = row["pin10"]
        rep = rep_for_pin10.get(pin10) if pin10 else None
        if rep is None:
            # Stranded condo unit PIN with no rep yet (shouldn't happen if
            # condo_rollup ran first, but be defensive).
            continue
        rep_to_idxs[rep].add(int(row["_idx"]))

    # ---- Per-rep reduction: pick the LARGEST-AREA structure for all fields.
    # Use shape_area as the area metric since bldg_sq_fo is gross floor area
    # (footprint × stories) and would over-weight tall garages.
    rep_to_footprint_summary: dict[str, dict] = {}
    for rep, idxs in rep_to_idxs.items():
        rows = [raw_rows[i] for i in idxs]
        primary = max(rows, key=lambda r: (r.get("shape_area") or 0.0))
        rep_to_footprint_summary[rep] = {
            "year_built": primary.get("year_built"),
            "building_sf": primary.get("bldg_sq_fo"),
            "condition_raw": primary.get("bldg_condi"),
            "unit_count": primary.get("no_of_unit"),
            "edit_date": primary.get("edit_date"),
        }

    # ---- Merge with assessor values per the rules above. We only merge for
    # parcels that have a footprint summary — i.e. building reps and non-condo
    # parcels. is_condo_unit PINs are intentionally skipped (their data
    # belongs on the rep).
    parcel_by_pin = {p["pin"]: p for p in parcels}
    updates: list[dict] = []
    for pin, p in parcel_by_pin.items():
        if p["is_condo_unit"]:
            continue
        fp = rep_to_footprint_summary.get(pin)
        a_year = p["year_built"]
        a_sf = p["building_sf"]
        a_cond = p["condition"]
        a_units = p["unit_count"]

        # Uniform merge rule: assessor wins when non-null, footprint backstops.
        def _merge_value(a_val, f_val):
            if a_val is not None:
                return a_val, "assessor"
            if f_val is not None:
                return f_val, "footprint"
            return None, None

        new_year, _ = _merge_value(a_year, fp["year_built"] if fp else None)
        new_sf, sf_src = _merge_value(a_sf, fp["building_sf"] if fp else None)
        new_units, _ = _merge_value(a_units, fp["unit_count"] if fp else None)

        # Condition: same rule, but translate footprint vocab to assessor's
        # when it fills a NULL.
        if a_cond:
            new_cond, cond_src = a_cond, "assessor"
        elif fp and fp["condition_raw"]:
            translated = _translate_condition(fp["condition_raw"])
            new_cond, cond_src = (translated, "footprint" if translated else None)
        else:
            new_cond, cond_src = None, None

        # Recompute FAR-derived fields whenever building_sf changes — without
        # this, far_gap and far_gap_delta stay anchored to assessor's old SF
        # even after footprint wins the merge. lot_size_sf and max_far are
        # set by ccgis_parcels and cdp_zoning earlier in the pipeline; we
        # don't change them here.
        lot = p["lot_size_sf"]
        max_far = p["max_far"]
        if new_sf and lot and lot > 0:
            new_built_far = round(new_sf / lot, 4)
        else:
            new_built_far = None
        if max_far is not None and new_built_far is not None and new_built_far > 0:
            new_far_gap = round(max_far / new_built_far, 4)
            new_far_gap_delta = round(max_far - new_built_far, 4)
        else:
            new_far_gap = None
            new_far_gap_delta = None

        # Always emit when this parcel had any footprint hit OR any of the
        # values would change. Writing on every match keeps the *_source
        # columns accurate even when the value itself didn't move (e.g. when
        # a previous run wrote 'footprint' and the current rule says
        # 'assessor' — same number, different provenance).
        had_footprint_match = fp is not None
        values_changed = (new_year != a_year or new_sf != a_sf
                or new_cond != a_cond or new_units != a_units)
        if had_footprint_match or values_changed:
            updates.append({
                "pin": pin,
                "year_built": new_year,
                "building_sf": new_sf,
                "condition": new_cond,
                "unit_count": new_units,
                "building_sf_source": sf_src,
                "condition_source": cond_src,
                "built_far": new_built_far,
                "far_gap": new_far_gap,
                "far_gap_delta": new_far_gap_delta,
                "fetched_at": fetched_at,
            })

    if updates:
        conn = get_connection(db_path)
        try:
            for u in updates:
                conn.execute("""
                    UPDATE parcels SET
                        year_built = :year_built,
                        building_sf = :building_sf,
                        condition = :condition,
                        unit_count = :unit_count,
                        building_sf_source = :building_sf_source,
                        condition_source = :condition_source,
                        built_far = :built_far,
                        far_gap = :far_gap,
                        far_gap_delta = :far_gap_delta,
                        last_updated_date = :fetched_at
                    WHERE pin = :pin
                """, u)
            conn.commit()
        finally:
            conn.close()
    return n
