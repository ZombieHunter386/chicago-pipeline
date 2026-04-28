"""Vectorized spatial matching of point records to parcels."""
from __future__ import annotations
from collections import defaultdict
from typing import Callable, Sequence

import geopandas as gpd
from shapely.geometry import Point

from pipeline.address import (
    DIRECTION_TOKENS,
    expand_address_range,
    fuzzy_distance,
    split_canonical,
    street_key,
)


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


# ============================================================
# Address-first matcher
# ============================================================

# Match-method tags. Listed in confidence order — callers should treat
# lower-indexed methods as more reliable.
METHOD_ADDRESS_EXACT = "address_exact"
METHOD_ADDRESS_RANGE = "address_range"
METHOD_GEO = "geo_75ft"
METHOD_FUZZY_REVIEW = "fuzzy_review"   # logged, not auto-matched

DEFAULT_GEO_RADIUS_FT = 75.0
DEFAULT_FUZZY_THRESHOLD = 2  # max Levenshtein distance between street-name tokens


def match_records_to_parcels_with_address(
    records: Sequence[dict],
    parcels: Sequence,
    *,
    get_record_address: Callable[[dict], str | None],
    record_lat_field: str = "latitude",
    record_lng_field: str = "longitude",
    parcel_pin_field: str = "pin",
    parcel_address_field: str = "address",
    parcel_lat_field: str = "lat",
    parcel_lng_field: str = "lng",
    geo_radius_ft: float = DEFAULT_GEO_RADIUS_FT,
    fuzzy_threshold: int = DEFAULT_FUZZY_THRESHOLD,
) -> tuple[dict[int, tuple[str, str]], list[dict]]:
    """Address-first match. Returns:
        (matched, fuzzy_review_rows)
      matched: {record_index: (pin, method)} — records that auto-matched.
      fuzzy_review_rows: records that almost-matched on address but didn't
        meet the auto-match bar (street name within Levenshtein N of a parcel
        on the same number+direction). Caller decides what to do with these.

    Match order:
      1. Exact normalized street-key match.
      2. Range expansion ("100-104 W DIVERSEY") — match if any parcel falls
         in the expanded set.
      3. Geo nearest within `geo_radius_ft`.
      4. Fuzzy: same number+direction, Levenshtein <= threshold on the rest
         of the canonical key. Logged for review, NOT auto-matched.

    Records without any match (no address, no lat/lng, no neighbor) are
    silently dropped — callers can compute the drop rate from len(matched)
    vs len(records).
    """
    # ----- Build parcel address indexes -----
    parcel_by_key: dict[str, str] = {}
    parcels_with_canon: list[tuple[str, str | None, str | None, tuple[str, ...], str | None]] = []
    for p in parcels:
        pin = p[parcel_pin_field]
        addr = p.get(parcel_address_field) if isinstance(p, dict) else p[parcel_address_field]
        key = street_key(addr)
        if key:
            # First-write-wins on duplicate addresses (rare; usually condo unit
            # PINs sharing a building address — caller can re-fanout via pin10).
            parcel_by_key.setdefault(key, pin)
            num, direction, _suffix, middle = split_canonical(key)
            parcels_with_canon.append((pin, num, direction, middle, key))

    # ----- Tier 1 + 2: exact and range -----
    matched: dict[int, tuple[str, str]] = {}
    unmatched_indices: list[int] = []
    for i, r in enumerate(records):
        addr = get_record_address(r)
        key = street_key(addr)
        if not key:
            unmatched_indices.append(i)
            continue
        if key in parcel_by_key:
            matched[i] = (parcel_by_key[key], METHOD_ADDRESS_EXACT)
            continue
        expanded = expand_address_range(key)
        if len(expanded) > 1:
            hit = next((parcel_by_key[e] for e in expanded if e in parcel_by_key), None)
            if hit is not None:
                matched[i] = (hit, METHOD_ADDRESS_RANGE)
                continue
        unmatched_indices.append(i)

    # ----- Tier 3: geo fallback for unmatched -----
    if unmatched_indices:
        unmatched_records = [records[i] for i in unmatched_indices]
        geo_hits = match_records_to_parcels(
            unmatched_records,
            parcels,
            geo_radius_ft,
            record_lat_field=record_lat_field,
            record_lng_field=record_lng_field,
            parcel_pin_field=parcel_pin_field,
            parcel_lat_field=parcel_lat_field,
            parcel_lng_field=parcel_lng_field,
        )
        # geo_hits is keyed by index-into-unmatched_records; remap to original.
        still_unmatched: list[int] = []
        for local_idx, orig_idx in enumerate(unmatched_indices):
            if local_idx in geo_hits:
                matched[orig_idx] = (geo_hits[local_idx], METHOD_GEO)
            else:
                still_unmatched.append(orig_idx)
        unmatched_indices = still_unmatched

    # ----- Tier 4: fuzzy — log for review, do not auto-match -----
    fuzzy_review: list[dict] = []
    if unmatched_indices and parcels_with_canon and fuzzy_threshold > 0:
        # Index parcels by (number, direction) for cheap candidate lookup.
        by_num_dir: dict[tuple[str | None, str | None], list[tuple[str, str]]] = defaultdict(list)
        for pin, num, direction, _middle, full_key in parcels_with_canon:
            by_num_dir[(num, direction)].append((pin, full_key))
        for orig_idx in unmatched_indices:
            r = records[orig_idx]
            addr = get_record_address(r)
            key = street_key(addr)
            if not key:
                continue
            num, direction, _suffix, _middle = split_canonical(key)
            candidates = by_num_dir.get((num, direction), [])
            if not candidates:
                continue
            best_pin = None
            best_dist = fuzzy_threshold + 1
            best_pkey = None
            for pin, pkey in candidates:
                d = fuzzy_distance(key, pkey)
                if d < best_dist:
                    best_dist = d
                    best_pin = pin
                    best_pkey = pkey
            if best_pin is not None and best_dist <= fuzzy_threshold:
                fuzzy_review.append({
                    "record_index": orig_idx,
                    "record_address": addr,
                    "record_key": key,
                    "candidate_pin": best_pin,
                    "candidate_key": best_pkey,
                    "levenshtein": best_dist,
                })

    return matched, fuzzy_review
