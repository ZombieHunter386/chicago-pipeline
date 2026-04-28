# pipeline/consolidate.py
"""Adjacent same-owner parcel consolidation."""
from __future__ import annotations
import json
from datetime import date
from pathlib import Path
from collections import defaultdict
from math import radians, sin, cos, sqrt, atan2
from pipeline.db import get_connection


ADJACENCY_RADIUS_FT = 200.0


def _haversine_ft(lat1, lng1, lat2, lng2):
    R = 6_371_000
    a1, a2 = radians(lat1), radians(lat2)
    da = radians(lat2 - lat1); dl = radians(lng2 - lng1)
    a = sin(da/2)**2 + cos(a1) * cos(a2) * sin(dl/2)**2
    c = 2 * atan2(sqrt(a), sqrt(1-a))
    return R * c * 3.28084


def _owner_key(row) -> tuple[str, str]:
    return ((row["owner_name"] or "").strip().upper(),
            (row["mail_address"] or "").strip().upper())


def _cluster(points: list[dict], radius: float) -> list[list[dict]]:
    """Single-link clustering on haversine distance."""
    remaining = points.copy()
    clusters: list[list[dict]] = []
    while remaining:
        seed = remaining.pop(0)
        cluster = [seed]
        changed = True
        while changed:
            changed = False
            still = []
            for p in remaining:
                near = any(_haversine_ft(p["lat"], p["lng"], q["lat"], q["lng"]) <= radius
                           for q in cluster)
                if near:
                    cluster.append(p)
                    changed = True
                else:
                    still.append(p)
            remaining = still
        clusters.append(cluster)
    return clusters


def consolidate(db_path: Path) -> int:
    """Detect adjacent same-owner parcel groups; return # groups created."""
    conn = get_connection(db_path)
    try:
        rows = conn.execute("""
            SELECT pin, lat, lng, lot_size_sf, building_sf, owner_name, mail_address
            FROM parcels
            WHERE lat IS NOT NULL AND lng IS NOT NULL AND owner_name IS NOT NULL
        """).fetchall()
    finally:
        conn.close()

    # Group by owner key
    by_owner: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in rows:
        by_owner[_owner_key(r)].append(dict(r))

    conn = get_connection(db_path)
    groups_created = 0
    try:
        # Wipe prior groupings so this is idempotent
        conn.execute("DELETE FROM consolidation_groups")
        conn.execute("UPDATE parcels SET consolidation_group_id = NULL")

        today = date.today().isoformat()
        for (owner, _), parcels in by_owner.items():
            if len(parcels) < 2:
                continue
            for cluster in _cluster(parcels, ADJACENCY_RADIUS_FT):
                if len(cluster) < 2:
                    continue
                pins = sorted(p["pin"] for p in cluster)
                total_lot = sum((p["lot_size_sf"] or 0) for p in cluster) or None
                total_bldg = sum((p["building_sf"] or 0) for p in cluster) or None
                cur = conn.execute("""
                    INSERT INTO consolidation_groups (pins, combined_lot_size_sf,
                                                      combined_building_sf, owner_name, detected_date)
                    VALUES (?, ?, ?, ?, ?)
                """, (json.dumps(pins), total_lot, total_bldg, owner, today))
                gid = cur.lastrowid
                for pin in pins:
                    conn.execute(
                        "UPDATE parcels SET consolidation_group_id = ? WHERE pin = ?",
                        (gid, pin),
                    )
                groups_created += 1
        conn.commit()
    finally:
        conn.close()
    return groups_created
