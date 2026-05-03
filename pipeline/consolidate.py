# pipeline/consolidate.py
"""Adjacent same-owner parcel consolidation.

Owner identity is keyed on mailing address when present, so name-spelling
variants on the same mailing address ("THOMAS J POZDOL" vs "THOMAS POZOOL"
at 7323 SCHOOL ST) get merged into one owner. To avoid over-merging at
property-manager / PO-box mail-drops where many distinct owners share one
forwarding address, mailing addresses with > MAIL_DROP_THRESHOLD distinct
owner-name spellings fall back to the legacy (name, mail) tuple key.
Within a non-mail-drop mailing-address bucket, owner-name variants are
fuzzy-clustered (token-sorted Levenshtein ratio OR shared first-3 chars
of the last token) before adjacency clustering."""
from __future__ import annotations
import json
from datetime import date
from pathlib import Path
from collections import defaultdict
from math import radians, sin, cos, sqrt, atan2
from pipeline.address import levenshtein
from pipeline.db import get_connection


ADJACENCY_RADIUS_FT = 200.0
# Mailing addresses with more than this many distinct owner-name spellings
# are treated as forwarding addresses (property managers, PO boxes,
# attorney offices). Falls back to the legacy (name, mail) key for these.
MAIL_DROP_THRESHOLD = 20
# Owner-name fuzzy-match threshold (Levenshtein ratio, 0..1).
NAME_RATIO_THRESHOLD = 0.8


def _haversine_ft(lat1, lng1, lat2, lng2):
    R = 6_371_000
    a1, a2 = radians(lat1), radians(lat2)
    da = radians(lat2 - lat1); dl = radians(lng2 - lng1)
    a = sin(da/2)**2 + cos(a1) * cos(a2) * sin(dl/2)**2
    c = 2 * atan2(sqrt(a), sqrt(1-a))
    return R * c * 3.28084


def _norm_name(name: str | None) -> str:
    """Token-sorted, single-char tokens dropped, upper, whitespace-collapsed.
    Drops stop tokens (single chars like middle initials) so "THOMAS J POZDOL"
    and "THOMAS POZDOL" normalize to the same string."""
    if not name:
        return ""
    tokens = [t for t in name.upper().strip().split() if len(t) > 1]
    return " ".join(sorted(tokens))


def _name_ratio(a: str, b: str) -> float:
    """Levenshtein similarity ratio in [0, 1]. 1.0 means identical."""
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    dist = levenshtein(a, b)
    ml = max(len(a), len(b))
    return 1.0 - (dist / ml) if ml else 0.0


def _last_token_prefix(norm: str, n: int = 3) -> str:
    """First n chars of the last (alphabetically sorted) token, or empty."""
    if not norm:
        return ""
    toks = norm.split()
    return toks[-1][:n] if toks else ""


def _names_match(name_a: str | None, name_b: str | None) -> bool:
    """Two owner names belong to the same person/entity if either:
      - Levenshtein ratio of their normalized forms is >= NAME_RATIO_THRESHOLD,
      - OR the last token's first 3 characters match.
    Either rule alone catches the "POZDOL"/"POZOOL" case; the OR makes the
    matcher resilient to one shorter form ("ABC PROPS" vs "ABC PROPERTIES")."""
    na = _norm_name(name_a)
    nb = _norm_name(name_b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    if _name_ratio(na, nb) >= NAME_RATIO_THRESHOLD:
        return True
    pa = _last_token_prefix(na)
    pb = _last_token_prefix(nb)
    return bool(pa) and pa == pb


def _build_owner_buckets(rows: list[dict]) -> dict[tuple, list[dict]]:
    """Group parcels into "same owner" buckets using mail-address-first logic.

    For each parcel, the bucket key is one of:
      - ("name", normalized_name, mail) — when mail is empty or a mail-drop
      - ("mail", mail, cluster_idx)     — when mail is non-mail-drop, with
        cluster_idx assigned by fuzzy-matching owner names within that mail."""
    # Step 1: count distinct normalized owner names per mailing address so we
    # can flag mail-drop addresses.
    mail_owner_set: dict[str, set[str]] = defaultdict(set)
    for r in rows:
        mail = (r["mail_address"] or "").strip().upper()
        name_n = _norm_name(r["owner_name"])
        if mail and name_n:
            mail_owner_set[mail].add(name_n)
    mail_drops = {m for m, owners in mail_owner_set.items()
                  if len(owners) > MAIL_DROP_THRESHOLD}

    # Step 2: bucket rows by mailing address (when usable), else by name+mail.
    by_mail: dict[str, list[dict]] = defaultdict(list)
    legacy_buckets: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in rows:
        mail = (r["mail_address"] or "").strip().upper()
        if mail and mail not in mail_drops:
            by_mail[mail].append(r)
        else:
            name_n = _norm_name(r["owner_name"])
            legacy_buckets[(name_n, mail)].append(r)

    # Step 3: within each mailing-address bucket, fuzzy-cluster owner names.
    buckets: dict[tuple, list[dict]] = {}
    for mail, parcels in by_mail.items():
        clusters: list[dict] = []
        for p in parcels:
            placed = False
            for c in clusters:
                if _names_match(p["owner_name"], c["rep_name"]):
                    c["parcels"].append(p)
                    placed = True
                    break
            if not placed:
                clusters.append({"rep_name": p["owner_name"], "parcels": [p]})
        for idx, c in enumerate(clusters):
            buckets[("mail", mail, idx)] = c["parcels"]

    for (name_n, mail), parcels in legacy_buckets.items():
        if not name_n:
            continue
        buckets[("name", name_n, mail)] = parcels

    return buckets


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
        rows = [dict(r) for r in conn.execute("""
            SELECT pin, lat, lng, lot_size_sf, building_sf, owner_name, mail_address
            FROM parcels
            WHERE lat IS NOT NULL AND lng IS NOT NULL AND owner_name IS NOT NULL
        """).fetchall()]
    finally:
        conn.close()

    by_owner = _build_owner_buckets(rows)

    conn = get_connection(db_path)
    groups_created = 0
    try:
        # Wipe prior groupings so this is idempotent
        conn.execute("DELETE FROM consolidation_groups")
        conn.execute("UPDATE parcels SET consolidation_group_id = NULL")

        today = date.today().isoformat()
        for parcels in by_owner.values():
            if len(parcels) < 2:
                continue
            # Group label: most common owner_name in the bucket (so display
            # shows a real name even when the bucket merged spelling variants).
            name_counts: dict[str, int] = defaultdict(int)
            for p in parcels:
                if p["owner_name"]:
                    name_counts[p["owner_name"]] += 1
            display_name = max(name_counts.items(), key=lambda kv: kv[1])[0] \
                if name_counts else ""
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
                """, (json.dumps(pins), total_lot, total_bldg, display_name, today))
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
