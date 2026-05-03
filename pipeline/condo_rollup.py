"""Roll condo unit parcels up to a single building-level row per pin10.

A pin10 is a "condo building" if any of its constituents has property_class
in CONDO_CLASSES. The lowest-numbered constituent PIN is designated the
building rep; financial columns (assessed_total/land/building, estimated_annual_tax)
are summed across all constituents and written onto the rep. Non-rep
constituents are flagged is_condo_unit=1 and hidden by default in the UI.

Idempotent across normal fetch_all runs: re-runs read raw_assessor_values to
recover per-PIN AVs after a previous rollup overwrote the rep's parcels row.
"""
from __future__ import annotations
from pathlib import Path

from pipeline.db import get_connection


# Cook County residential condo class codes:
#   290 = condo land/garage/parking, 295 = condo conversion,
#   297 = multi-residential condo, 299 = residential condo unit
CONDO_CLASSES = ("290", "295", "297", "299")


def rollup_condos(db_path: Path) -> int:
    """Apply condo rollup. Returns count of buildings rolled up."""
    conn = get_connection(db_path)
    try:
        snapshot = {
            r["pin"]: dict(r) for r in conn.execute(
                "SELECT pin, pin10, property_class, assessed_total, "
                "       assessed_land, assessed_building, estimated_annual_tax, "
                "       building_sf, is_condo_building "
                "FROM parcels"
            )
        }

        # Reset all condo flags so this is idempotent.
        conn.execute(
            "UPDATE parcels SET is_condo_unit = 0, is_condo_building = 0, "
            "       condo_unit_count = NULL, "
            "       condo_units_missing_sf_count = NULL"
        )

        # For previous reps, the snapshot's summed columns reflect the prior
        # rollup. Restore per-PIN values from raw tables so re-runs don't
        # double-count: AV from raw_assessor_values, building_sf from
        # raw_assessor_characteristics (latest year).
        prev_reps = [pin for pin, r in snapshot.items() if r["is_condo_building"]]
        if prev_reps:
            placeholders = ",".join("?" * len(prev_reps))
            for r in conn.execute(
                f"SELECT pin, board_tot, certified_tot, mailed_tot, board_land, "
                f"       certified_land, mailed_land, board_bldg, certified_bldg, "
                f"       mailed_bldg, year "
                f"FROM raw_assessor_values WHERE pin IN ({placeholders}) "
                f"ORDER BY pin, year DESC",
                prev_reps,
            ):
                pin = r["pin"]
                if snapshot.get(pin, {}).get("_av_reset"):
                    continue
                tot = r["board_tot"] or r["certified_tot"] or r["mailed_tot"]
                land = r["board_land"] or r["certified_land"] or r["mailed_land"]
                bldg = r["board_bldg"] or r["certified_bldg"] or r["mailed_bldg"]
                if tot is None:
                    continue
                snapshot[pin]["assessed_total"] = tot
                snapshot[pin]["assessed_land"] = land
                snapshot[pin]["assessed_building"] = bldg
                snapshot[pin]["_av_reset"] = True

            for r in conn.execute(
                f"SELECT pin, char_bldg_sf, year FROM raw_assessor_characteristics "
                f"WHERE pin IN ({placeholders}) ORDER BY pin, year DESC",
                prev_reps,
            ):
                pin = r["pin"]
                if snapshot.get(pin, {}).get("_bldg_sf_reset"):
                    continue
                snapshot[pin]["building_sf"] = r["char_bldg_sf"]
                snapshot[pin]["_bldg_sf_reset"] = True

        condo_pin10s = sorted({
            r["pin10"] for r in snapshot.values()
            if r["pin10"] and r["property_class"] in CONDO_CLASSES
        })
        if not condo_pin10s:
            return 0

        groups_rolled = 0
        for pin10 in condo_pin10s:
            rows = sorted(
                (r for r in snapshot.values() if r["pin10"] == pin10),
                key=lambda r: r["pin"],
            )
            if not rows:
                continue
            rep_pin = rows[0]["pin"]
            unit_pins = [r["pin"] for r in rows[1:]]

            sum_at = sum((r["assessed_total"] or 0) for r in rows) or None
            sum_al = sum((r["assessed_land"] or 0) for r in rows) or None
            sum_ab = sum((r["assessed_building"] or 0) for r in rows) or None
            sum_et = sum((r["estimated_annual_tax"] or 0) for r in rows) or None
            sum_bldg_sf = sum((r["building_sf"] or 0) for r in rows) or None
            missing_sf = sum(1 for r in rows if r["building_sf"] is None)

            conn.execute(
                "UPDATE parcels SET "
                "  is_condo_building = 1, "
                "  condo_unit_count = ?, "
                "  condo_units_missing_sf_count = ?, "
                "  assessed_total = ?, "
                "  assessed_land = ?, "
                "  assessed_building = ?, "
                "  estimated_annual_tax = ?, "
                "  building_sf = ? "
                "WHERE pin = ?",
                (len(rows), missing_sf, sum_at, sum_al, sum_ab, sum_et,
                 sum_bldg_sf, rep_pin),
            )
            if unit_pins:
                placeholders = ",".join("?" * len(unit_pins))
                conn.execute(
                    f"UPDATE parcels SET is_condo_unit = 1 "
                    f"WHERE pin IN ({placeholders})",
                    unit_pins,
                )
            groups_rolled += 1

        conn.commit()
        return groups_rolled
    finally:
        conn.close()
