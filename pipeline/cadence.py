"""Cadence engine — pure functions over outreach state.

Three pure functions (no DB) for the cadence rules, plus an orchestrator
(in this same file, added in Task 4) that hits the DB. The pure functions
are trivially unit-testable; the orchestrator is the only function that
needs a DB fixture.
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


def _contact_has(
    contact: dict | None,
    parcel_mail_address: str | None,
    field: str,
) -> bool:
    """True iff the named contact field is non-empty.

    mail_address comes from the parcel (assessor data, always present in
    practice); email/phone come from the contact row.
    """
    if field == "mail_address":
        return bool(parcel_mail_address)
    if not contact:
        return False
    return bool(contact.get(field))


def next_due_touches_for_parcel(
    *,
    cadence_config: dict,
    outreach_rows: list[dict],
    contact: dict | None,
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

    Each returned item is the touch config dict augmented with:
      target_date  (ISO date string)
      days_overdue (int, 0 if due today, positive if past)
    """
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
        if not _contact_has(contact, parcel_mail_address, tpl["requires"]):
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
    grace = cadence_config.get("end_of_sequence_grace_days", 0)
    return (today - last_date).days >= grace
