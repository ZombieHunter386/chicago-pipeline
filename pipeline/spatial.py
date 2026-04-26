"""Vectorized spatial matching of point records to parcels."""
from __future__ import annotations
from typing import Sequence

import geopandas as gpd
from shapely.geometry import Point


# NAD83 Illinois East (US survey feet) — planar CRS suited to Cook County so
# the radius_ft is honored exactly without spherical-distance approximations.
PLANAR_CRS = "EPSG:3435"


def match_records_to_parcels(
    records: Sequence[dict],
    parcels: Sequence,
    radius_ft: float,
    record_lat_field: str = "latitude",
    record_lng_field: str = "longitude",
    parcel_pin_field: str = "pin",
    parcel_lat_field: str = "lat",
    parcel_lng_field: str = "lng",
) -> dict[int, str]:
    """Match each record (by index) to its nearest parcel within radius_ft.

    Returns: {record_index: pin}. Records with missing lat/lng are skipped.
    Records with no parcel within radius_ft are not included in the result.
    """
    if not records or not parcels:
        return {}

    parcel_features = [
        {parcel_pin_field: p[parcel_pin_field],
         "geometry": Point(p[parcel_lng_field], p[parcel_lat_field])}
        for p in parcels
        if p[parcel_lat_field] is not None and p[parcel_lng_field] is not None
    ]
    if not parcel_features:
        return {}
    parcels_gdf = gpd.GeoDataFrame(parcel_features, crs="EPSG:4326").to_crs(PLANAR_CRS)

    record_features = []
    for i, r in enumerate(records):
        lat = r.get(record_lat_field)
        lng = r.get(record_lng_field)
        if lat is None or lng is None:
            continue
        record_features.append({"_idx": i, "geometry": Point(lng, lat)})
    if not record_features:
        return {}
    records_gdf = gpd.GeoDataFrame(record_features, crs="EPSG:4326").to_crs(PLANAR_CRS)

    joined = gpd.sjoin_nearest(
        records_gdf, parcels_gdf,
        how="inner", max_distance=radius_ft, distance_col="_dist",
    )
    # If a record ties at exactly the same distance to two parcels, sjoin_nearest
    # returns both rows; keep the first.
    joined = joined.drop_duplicates(subset=["_idx"], keep="first")
    return {int(row["_idx"]): row[parcel_pin_field] for _, row in joined.iterrows()}
