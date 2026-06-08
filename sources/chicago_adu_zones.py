"""Source: City of Chicago ADU Eligibility Map.

Fetches polygons from the City's ArcGIS REST endpoint:
  https://services7.arcgis.com/A03QrhyHnDaUmK0W/arcgis/rest/services/ADUAllowedRS2AA_view/FeatureServer/0

Each polygon represents an RS-zoned area where ADUs are allowed (the
2021 ordinance restricts RS-zoned ADUs to these designated areas).
Each polygon also carries a `Text` field describing the per-area
restrictions (annual block caps, owner-occupancy requirements, etc.).

This source:
  1. Fetches the polygons into raw_chicago_adu_zones.
  2. Spatial-joins each parcel's centroid against the polygons.
  3. Derives parcels.adu_eligible + parcels.adu_restriction_text +
     parcels.adu_has_annual_limits.

Refresh cadence: monthly. The City publishes updates infrequently;
operators can re-run this source at will without affecting other data.
"""
from __future__ import annotations

from datetime import datetime, UTC
from pathlib import Path

import requests
from shapely.geometry import Point, shape
from shapely import wkt as wkt_lib
from shapely.validation import make_valid

from pipeline.db import get_connection


# Zone-class prefixes that are ADU-eligible citywide (no polygon lookup
# needed). Per the City's instructions:
#   "If the zoning is RT, RM, any B, C1 or C2 — you are eligible for an ADU!"
CITYWIDE_ELIGIBLE_PREFIXES = ("RT-", "RM-", "B", "C1-", "C2-")

# RS zones are conditionally eligible — only when inside an ADU-Allowed RS polygon.
RS_ZONES = ("RS-1", "RS-2", "RS-3")

ARCGIS_QUERY_URL = (
    "https://services7.arcgis.com/A03QrhyHnDaUmK0W/arcgis/rest/services/"
    "ADUAllowedRS2AA_view/FeatureServer/0/query"
)


def derive_adu_eligible(zone_class: str | None, in_rs_polygon: bool) -> int:
    """Return 1 if a parcel with this zone is ADU-eligible, else 0.

    Rules (from chicago.gov/adu instructions):
      - RT-*, RM-*, B*, C1-*, C2-* → eligible citywide (1)
      - RS-1, RS-2, RS-3 → eligible only if inside an ADU-Allowed RS polygon
      - Everything else (M-*, PD, C3+, etc.) → not eligible (0)
    """
    z = (zone_class or "").upper()
    if not z:
        return 0
    if z.startswith(CITYWIDE_ELIGIBLE_PREFIXES):
        return 1
    if z in RS_ZONES:
        return 1 if in_rs_polygon else 0
    return 0


def _query_params() -> dict:
    return {
        "where": "1=1",
        "outFields": "ADU_Area,Zone,Text",
        "returnGeometry": "true",
        "outSR": "4326",        # WGS84 — same CRS as parcels.lat/lng
        "f": "json",
    }


def fetch(db_path: Path) -> int:
    """Fetch all polygons from the City's ADU eligibility layer, persist
    to raw_chicago_adu_zones. Returns the number of polygons written.

    Idempotent: zone_id is the primary key, so re-running upserts in place.
    Safe to run as a monthly refresh — the City's polygons rarely change.
    """
    fetched_at = datetime.now(UTC).isoformat(timespec="seconds")
    resp = requests.get(ARCGIS_QUERY_URL, params=_query_params(), timeout=30)
    resp.raise_for_status()
    data = resp.json()
    features = data.get("features", []) or []

    rows = []
    for f in features:
        attrs = f.get("attributes", {}) or {}
        geom_dict = f.get("geometry")
        if not geom_dict:
            continue
        # ArcGIS REST returns rings in the 'rings' key for polygon geometry.
        # Convert to GeoJSON polygon shape that shapely understands.
        rings = geom_dict.get("rings")
        if not rings:
            continue
        geojson = {"type": "Polygon", "coordinates": rings}
        try:
            geom = shape(geojson)
        except Exception:
            continue
        zone_id = str(attrs.get("Zone") or "")
        if not zone_id:
            continue
        rows.append({
            "zone_id": zone_id,
            "adu_area_code": attrs.get("ADU_Area") or "",
            "restriction_text": attrs.get("Text") or "",
            "polygon_wkt": geom.wkt,
            "fetched_at": fetched_at,
        })

    conn = get_connection(db_path)
    try:
        for r in rows:
            conn.execute(
                "INSERT OR REPLACE INTO raw_chicago_adu_zones "
                "(zone_id, adu_area_code, restriction_text, polygon_wkt, fetched_at) "
                "VALUES (:zone_id, :adu_area_code, :restriction_text, "
                ":polygon_wkt, :fetched_at)",
                r,
            )
        conn.commit()
    finally:
        conn.close()
    return len(rows)


def apply_to_parcels(db_path: Path) -> int:
    """For every parcel with a (lat, lng), determine ADU eligibility:
      - parcels.adu_eligible: 1 if zone_class is citywide-eligible OR
        (zone_class is RS-1/2/3 AND centroid is inside any ADU-Allowed
        RS polygon). 0 otherwise.
      - parcels.adu_restriction_text: the Text field of the containing
        polygon (for RS-in-polygon cases); NULL for citywide-eligible
        and non-eligible parcels.
      - parcels.adu_has_annual_limits: 1 if restriction_text contains
        'Annual Limits', else 0.

    Returns the number of parcels processed (each with-lat/lng parcel gets
    an UPDATE, even if its values didn't change from the prior run).

    Implementation: load all polygons into memory (small — typically <100
    polygons), iterate parcels and test point-in-polygon. shapely's
    contains() is exact; tens of thousands of point-vs-polygon tests
    complete in seconds.
    """
    conn = get_connection(db_path)
    try:
        polygons = [
            {
                "zone_id": r["zone_id"],
                "restriction_text": r["restriction_text"],
                # make_valid() fixes self-intersecting rings that ArcGIS REST
                # sometimes exports. Without it, contains() silently returns
                # False on invalid polygons even for interior points.
                "geom": make_valid(wkt_lib.loads(r["polygon_wkt"])),
            }
            for r in conn.execute(
                "SELECT zone_id, restriction_text, polygon_wkt "
                "FROM raw_chicago_adu_zones"
            )
        ]

        parcels = conn.execute(
            "SELECT pin, zone_class, lat, lng FROM parcels "
            "WHERE lat IS NOT NULL AND lng IS NOT NULL"
        ).fetchall()

        n_processed = 0
        for p in parcels:
            pt = Point(p["lng"], p["lat"])
            z = (p["zone_class"] or "").upper()
            in_rs_polygon = False
            restriction_text = None
            if z in RS_ZONES:
                for poly in polygons:
                    if poly["geom"].contains(pt):
                        in_rs_polygon = True
                        restriction_text = poly["restriction_text"] or None
                        break

            eligible = derive_adu_eligible(p["zone_class"], in_rs_polygon)
            has_annual_limits = 1 if (
                restriction_text and "Annual Limits" in restriction_text
            ) else 0
            conn.execute(
                "UPDATE parcels SET "
                "  adu_eligible = ?, "
                "  adu_restriction_text = ?, "
                "  adu_has_annual_limits = ? "
                "WHERE pin = ?",
                (eligible, restriction_text, has_annual_limits, p["pin"]),
            )
            n_processed += 1
        conn.commit()
    finally:
        conn.close()
    return n_processed
