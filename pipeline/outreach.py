"""Outreach domain logic — template loading, rendering, DB helpers.

Pure functions over a sqlite3.Connection. No Flask, no Google libs.
"""
from __future__ import annotations
import os
import re
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

import yaml


_VAR_RE = re.compile(r"\{\{\s*(\w+)\s*\}\}")
_LLC_TOKENS = {"LLC", "INC", "CORP", "CO", "LP", "LTD", "TRUST", "TRUSTEE", "PARTNERS"}


def load_templates(path: Path) -> dict[str, Any]:
    """Load outreach_templates.yaml. Returns a dict with keys:
      templates: {name -> {label, subject, body}}
      defaults: {my_name, my_email, my_phone, ...}
    """
    with Path(path).open() as f:
        data = yaml.safe_load(f) or {}
    templates_list = data.get("templates", []) or []
    templates = {t["name"]: t for t in templates_list if "name" in t}
    return {"templates": templates, "defaults": data.get("defaults") or {}}


def render_template(text: str, context: dict[str, Any]) -> str:
    """Replace {{var}} occurrences. Missing vars stay as literal {{var}} so the
    user can see what they need to fill in. Whitespace inside braces is tolerated.
    """
    def replace(m: re.Match[str]) -> str:
        var = m.group(1)
        if var in context and context[var] is not None:
            return str(context[var])
        return m.group(0)
    return _VAR_RE.sub(replace, text)


def sanitize_subject(subject: str) -> str:
    """Strip CR/LF from email subject lines (header-injection guard)."""
    return subject.replace("\r", "").replace("\n", "")


def _owner_first_name(owner_name: str | None) -> str:
    """Best-effort first name from a parsed Assessor owner string.

    Owner strings are ALL CAPS. Common forms:
      "JOHN SMITH"            -> "John"
      "SMITH, JOHN"           -> "John"
      "JOHN & MARY SMITH"     -> "John"
      "ACME PROPERTIES LLC"   -> "there"   (entity, no person)
    """
    if not owner_name:
        return "there"
    tokens = re.split(r"[\s,&/]+", owner_name.strip())
    tokens = [t for t in tokens if t]
    if not tokens:
        return "there"
    if any(t.upper() in _LLC_TOKENS for t in tokens):
        return "there"
    # "SMITH, JOHN" — comma form: first name is the token after the comma
    if "," in owner_name:
        parts = [p.strip() for p in owner_name.split(",", 1)]
        if len(parts) == 2 and parts[1]:
            first = parts[1].split()[0]
            return first.capitalize()
    # Otherwise first token is the first name.
    return tokens[0].capitalize()


def parcel_context(parcel: dict[str, Any], defaults: dict[str, Any]) -> dict[str, str]:
    """Build the merge-variable context for a parcel.

    String values only — render_template stringifies everything anyway; doing it
    here means tests don't have to assert on float repr quirks.
    """
    def s(v: Any) -> str:
        if v is None:
            return ""
        if isinstance(v, float):
            # Keep small decimals readable.
            return f"{v:.1f}".rstrip("0").rstrip(".") or "0"
        return str(v)

    ctx: dict[str, str] = {
        "owner_name": s(parcel.get("owner_name")),
        "owner_first_name": _owner_first_name(parcel.get("owner_name")),
        "address": s(parcel.get("address")),
        "ward": s(parcel.get("ward_num")),
        "zip": s(parcel.get("zip_code")),
        "score": s(parcel.get("score")),
        "property_class": s(parcel.get("property_class")),
        "year_built": s(parcel.get("year_built")),
        "building_sf": s(parcel.get("building_sf")),
        "lot_size_sf": s(parcel.get("lot_size_sf")),
    }
    for k, v in defaults.items():
        ctx.setdefault(k, s(v))
    return ctx


def upsert_contact(
    conn: sqlite3.Connection,
    *,
    pin: str,
    email: str | None = None,
    name: str | None = None,
    phone: str | None = None,
    mailing_address: str | None = None,
    role: str | None = None,
    source: str | None = None,
) -> int:
    """Upsert by pin (one contact row per pin in v1). Returns the contact_id.

    Only non-None fields are written — passing email=None on an update preserves
    the existing email value. This matters because the UI may upsert just an
    email today, then just a phone later.

    Requires the caller's connection to have `conn.row_factory = sqlite3.Row`
    (the standard for this project — see webapp/routes.py:_conn and
    pipeline/db.py:get_connection).
    """
    row = conn.execute(
        "SELECT contact_id FROM contacts WHERE pin = ? LIMIT 1", (pin,)
    ).fetchone()
    fields = {
        "email": email, "name": name, "phone": phone,
        "mailing_address": mailing_address, "role": role, "source": source,
    }
    fields = {k: v for k, v in fields.items() if v is not None}
    if row is None:
        cols = ["pin"] + list(fields.keys())
        placeholders = ",".join("?" * len(cols))
        params = [pin] + list(fields.values())
        cur = conn.execute(
            f"INSERT INTO contacts ({','.join(cols)}) VALUES ({placeholders})",
            params,
        )
        conn.commit()
        return int(cur.lastrowid)
    if fields:
        sets = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(
            f"UPDATE contacts SET {sets} WHERE contact_id = ?",
            list(fields.values()) + [row["contact_id"]],
        )
        conn.commit()
    return int(row["contact_id"])


def create_outreach_record(
    conn: sqlite3.Connection,
    *,
    pin: str,
    contact_id: int | None,
    channel: str,
    subject: str,
    body: str,
    sent_date: str,
    touch_number: int = 1,
) -> int:
    """Insert an outreach row. Returns the outreach_id."""
    cur = conn.execute(
        """
        INSERT INTO outreach
            (pin, contact_id, channel, touch_number, sent_date,
             draft_subject, draft_body, final_body)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (pin, contact_id, channel, touch_number, sent_date, subject, body, body),
    )
    conn.commit()
    return int(cur.lastrowid)


def list_outreach_for_parcel(
    conn: sqlite3.Connection, pin: str
) -> list[sqlite3.Row]:
    """Return outreach rows for a parcel, most recent first."""
    return conn.execute(
        """
        SELECT * FROM outreach
        WHERE pin = ?
        ORDER BY COALESCE(sent_date, '') DESC, outreach_id DESC
        """,
        (pin,),
    ).fetchall()


def mark_replied(
    conn: sqlite3.Connection,
    outreach_id: int,
    *,
    response_date: str,
    response_type: str = "responded",
) -> None:
    """Mark an outreach row as replied."""
    conn.execute(
        "UPDATE outreach SET response_date = ?, response_type = ? "
        "WHERE outreach_id = ?",
        (response_date, response_type, outreach_id),
    )
    conn.commit()


def save_template(
    path: Path,
    *,
    name: str,
    subject: str,
    body: str,
    label: str | None = None,
) -> dict[str, Any]:
    """Update an existing template by name or create a new one. Atomic write
    via temp file + os.replace so a partial write can't corrupt the YAML.

    Multi-line bodies are written as literal block scalars (`body: |`) so the
    file stays readable. The standard comment header at the top of the file
    is restored on every write (PyYAML strips comments on round-trip).

    Returns the saved template dict.
    """
    path = Path(path)
    raw = yaml.safe_load(path.read_text()) if path.exists() else None
    raw = raw or {}
    templates_list = list(raw.get("templates") or [])
    defaults = raw.get("defaults") or {}

    found = False
    for tpl in templates_list:
        if tpl.get("name") == name:
            tpl["subject"] = subject
            tpl["body"] = body
            if label is not None:
                tpl["label"] = label
            found = True
            break

    if not found:
        templates_list.append({
            "name": name,
            "label": label or name,
            "subject": subject,
            "body": body,
        })

    _write_templates_yaml(path, {"templates": templates_list, "defaults": defaults})
    return next(t for t in templates_list if t.get("name") == name)


_YAML_HEADER = (
    "# Email templates used by the outreach compose modal.\n"
    "# Variables (rendered with {{var}} regex substitution):\n"
    "#   {{owner_name}}, {{owner_first_name}}, {{address}}, {{ward}}, {{zip}},\n"
    "#   {{score}}, {{property_class}}, {{year_built}}, {{building_sf}}, {{lot_size_sf}},\n"
    "#   {{my_name}}, {{my_email}}, {{my_phone}}\n"
    "# Missing variables render as literal \"{{name}}\" so you'll see them in preview.\n"
)


class _BlockScalarDumper(yaml.SafeDumper):
    """SafeDumper subclass that writes multi-line strings as literal block
    scalars (`|`) instead of the default quoted style — far more readable
    when a human opens the YAML. Also forces sequence items to indent under
    their parent key (`templates:\\n  - name: ...`) instead of PyYAML's
    indent-less default; cleaner diffs and matches conventional style."""

    def increase_indent(self, flow=False, indentless=False):
        return super().increase_indent(flow, False)


def _str_block_scalar_representer(dumper: yaml.Dumper, value: str):
    if "\n" in value:
        return dumper.represent_scalar(
            "tag:yaml.org,2002:str", value, style="|"
        )
    return dumper.represent_scalar("tag:yaml.org,2002:str", value)


_BlockScalarDumper.add_representer(str, _str_block_scalar_representer)


def _write_templates_yaml(path: Path, data: dict[str, Any]) -> None:
    """Write the templates YAML atomically. Temp file goes in the same dir
    so os.replace is atomic on the same filesystem."""
    yaml_body = yaml.dump(
        data,
        Dumper=_BlockScalarDumper,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
    )
    final = _YAML_HEADER + yaml_body
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", dir=str(path.parent), delete=False,
        prefix=".outreach_templates.", suffix=".tmp",
    ) as f:
        f.write(final)
        tmp_path = Path(f.name)
    try:
        os.replace(tmp_path, path)
    except OSError:
        tmp_path.unlink(missing_ok=True)
        raise
