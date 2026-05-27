"""Cadence engine — pure functions over outreach state.

Three public pure functions (load_cadence_config, next_due_touches_for_parcel,
is_end_of_sequence) plus two private helpers. No DB, no Flask. The orchestrator
(`all_due_touches`) is added in Task 4 — it's the only function in this file
that touches SQLite. The pure functions are unit-testable without fixtures.
"""
from __future__ import annotations
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import yaml


# Channels recognized by the engine. Adding new channels means updating the
# UI (channel-aware compose) and the digest format.
CHANNELS = {"email", "phone", "mail"}

# Fields the `requires` value can name. `mail_address` is sourced from
# parcels.mail_address (always present from assessor); the others from
# contacts.
REQUIRES_FIELDS = {"email", "phone", "mail_address"}


def load_cadence_config(path: Path) -> dict[str, Any]:
    """Load and validate config/outreach_cadence.yaml.

    Returns:
        {
          "sequence": [
              {"touch": int, "day_offset": int, "channel": str,
               "template": str, "requires": str},
              ...
          ],
          "end_of_sequence_action": str,
          "end_of_sequence_grace_days": int,
        }

    Raises ValueError on structural problems.
    """
    with Path(path).open() as f:
        data = yaml.safe_load(f) or {}
    sequence = data.get("sequence") or []
    if not isinstance(sequence, list) or not sequence:
        raise ValueError("cadence config must have a non-empty 'sequence' list")
    for tpl in sequence:
        for k in ("touch", "day_offset", "channel", "template", "requires"):
            if k not in tpl:
                raise ValueError(f"cadence touch missing required field {k!r}")
        if tpl["channel"] not in CHANNELS:
            raise ValueError(
                f"unknown channel {tpl['channel']!r}; allowed: {sorted(CHANNELS)}"
            )
        if tpl["requires"] not in REQUIRES_FIELDS:
            raise ValueError(
                f"unknown requires {tpl['requires']!r}; allowed: {sorted(REQUIRES_FIELDS)}"
            )
    sequence = sorted(sequence, key=lambda t: t["touch"])
    return {
        "sequence": sequence,
        "end_of_sequence_action": data.get("end_of_sequence_action") or "surface_for_dead",
        "end_of_sequence_grace_days": int(data.get("end_of_sequence_grace_days", 0)),
    }


def _parse_iso_date(s: str) -> date:
    """Parse 'YYYY-MM-DD' or 'YYYY-MM-DDTHH:MM:SSZ' into a date."""
    return date.fromisoformat(s[:10])


def next_due_touches_for_parcel(
    *,
    cadence_config: dict,
    outreach_rows: list[dict],
    contact: dict | None = None,        # backwards-compat, single contact row
    contacts: list[dict] | None = None, # new: multiple rows (Task 3)
    parcel_mail_address: str | None,
    today: date,
) -> list[dict]:
    """Return touch configs that are due/overdue for this parcel.

    Anchor = sent_date on the row where touch_number == 1. No touch_1 row →
    parcel hasn't entered cadence → empty result.

    For each touch in cadence_config["sequence"]:
      - skip if already done (a row exists with that touch_number)
      - skip if target_date > today (not yet due)
      - skip if `requires` field not satisfied

    Accepts either `contact` (single row, legacy callers) or `contacts` (list
    of rows, post–enrichment-rewrite callers). At least one alive email row is
    required to satisfy requires='email'; same for phone. Dead rows (dead=1 or
    wrong_person=1) are filtered out before the requires check via
    alive_emails_for_parcel / alive_phones_for_parcel.

    Each returned item is the touch config dict augmented with:
      target_date  (ISO date string)
      days_overdue (int, 0 if due today, positive if past)
    """
    if contacts is None:
        contacts = [contact] if contact else []

    # Function-level import to avoid a top-of-module cycle: enrichment.py may
    # eventually import cadence pieces, and cadence already imports outreach
    # in all_due_touches the same way.
    from pipeline.enrichment import (
        alive_emails_for_parcel,
        alive_phones_for_parcel,
    )
    has_email = bool(alive_emails_for_parcel(contacts))
    has_phone = bool(alive_phones_for_parcel(contacts))

    by_touch = {
        r["touch_number"]: r
        for r in outreach_rows
        if r.get("touch_number") is not None
    }
    anchor_row = by_touch.get(1)
    if not anchor_row or not anchor_row.get("sent_date"):
        return []
    anchor = _parse_iso_date(anchor_row["sent_date"])

    out = []
    for tpl in cadence_config["sequence"]:
        if tpl["touch"] in by_touch:
            continue
        target = anchor + timedelta(days=tpl["day_offset"])
        if target > today:
            continue
        requires = tpl["requires"]
        if requires == "email":
            if not has_email:
                continue
        elif requires == "phone":
            if not has_phone:
                continue
        elif requires == "mail_address":
            if not parcel_mail_address:
                continue
        out.append({
            **tpl,
            "target_date": target.isoformat(),
            "days_overdue": (today - target).days,
        })
    return out


def is_end_of_sequence(
    *,
    cadence_config: dict,
    outreach_rows: list[dict],
    today: date,
) -> bool:
    """True iff every touch in the sequence has been completed AND the grace
    period has elapsed since the last one was sent. Surfaces the 'mark dead?'
    prompt in the digest and UI."""
    by_touch = {
        r["touch_number"]: r
        for r in outreach_rows
        if r.get("touch_number") is not None
    }
    sequence = cadence_config["sequence"]
    if not all(t["touch"] in by_touch for t in sequence):
        return False
    last_touch_num = max(t["touch"] for t in sequence)
    last_row = by_touch[last_touch_num]
    if not last_row.get("sent_date"):
        return False
    last_date = _parse_iso_date(last_row["sent_date"])
    grace = cadence_config["end_of_sequence_grace_days"]
    return (today - last_date).days >= grace


def all_due_touches(conn, cadence_config: dict, today: date) -> dict:
    """DB-touching orchestrator. Queries parcels in `outreach` stage (and
    not paused), joins contacts + outreach history, runs the pure functions,
    returns the JSON-shaped structure documented in the spec.
    """
    # Import here to avoid a circular import between outreach.py and cadence.py.
    from pipeline.outreach import parcel_context

    rows = conn.execute(
        """
        SELECT pin, address, owner_name, mail_address, score,
               COALESCE(outreach_paused, 0) AS outreach_paused
        FROM parcels
        WHERE stage = 'outreach'
          AND COALESCE(outreach_paused, 0) = 0
        """
    ).fetchall()

    groups = {"email": [], "phone": [], "mail": [], "end_of_sequence": []}

    # Function-level import: keeps the orchestrator self-contained at
    # call-time and avoids paying for the import when only the pure helpers
    # are used (tests import this module without enrichment available).
    from pipeline.enrichment import (
        alive_emails_for_parcel,
        alive_phones_for_parcel,
    )

    for p in rows:
        pin = p["pin"]
        contact_rows = conn.execute(
            "SELECT * FROM contacts WHERE pin = ?", (pin,)
        ).fetchall()
        contacts = [dict(r) for r in contact_rows]
        outreach_rows = [
            dict(r) for r in conn.execute(
                "SELECT * FROM outreach WHERE pin = ? ORDER BY touch_number",
                (pin,),
            )
        ]
        owner_first = parcel_context(dict(p), {})["owner_first_name"]

        due = next_due_touches_for_parcel(
            cadence_config=cadence_config,
            outreach_rows=outreach_rows,
            contacts=contacts,
            parcel_mail_address=p["mail_address"],
            today=today,
        )
        alive_emails = alive_emails_for_parcel(contacts)
        alive_phones = alive_phones_for_parcel(contacts)
        for d in due:
            item = {
                "pin": pin,
                "address": p["address"],
                "owner_name": p["owner_name"],
                "owner_first_name": owner_first,
                "touch": d["touch"],
                "channel": d["channel"],  # self-describing — downstream code can read directly
                "template": d["template"],
                "target_date": d["target_date"],
                "days_overdue": d["days_overdue"],
            }
            if d["channel"] == "email":
                # First alive email = primary To: address. The full alive list
                # is provided so downstream (T4 BCC fanout) can BCC the rest.
                if alive_emails:
                    item["to_email"] = alive_emails[0]
                    item["bcc_emails"] = alive_emails[1:]
            elif d["channel"] == "phone":
                if alive_phones:
                    item["to_phone"] = alive_phones[0]
            elif d["channel"] == "mail":
                # Mail address always present from assessor data — populate the
                # target so the digest and compose UI don't have to refetch.
                item["to_mail_address"] = p["mail_address"]
            groups[d["channel"]].append(item)

        if is_end_of_sequence(
            cadence_config=cadence_config,
            outreach_rows=outreach_rows,
            today=today,
        ):
            by_touch = {
                r["touch_number"]: r for r in outreach_rows
                if r.get("touch_number") is not None
            }
            last_n = max(by_touch)
            last_row = by_touch[last_n]
            last_date = last_row["sent_date"][:10] if last_row.get("sent_date") else None
            days_since = (
                (today - _parse_iso_date(last_row["sent_date"])).days
                if last_row.get("sent_date") else 0
            )
            groups["end_of_sequence"].append({
                "pin": pin,
                "address": p["address"],
                "owner_first_name": owner_first,
                "last_touch_date": last_date,
                "days_since_last": days_since,
                "suggest": "mark_dead",
            })

    response_groups = []
    for channel in ("email", "phone", "mail", "end_of_sequence"):
        items = groups[channel]
        if items:
            response_groups.append({
                "channel": channel,
                "count": len(items),
                "items": items,
            })
    return {"today": today.isoformat(), "groups": response_groups}
