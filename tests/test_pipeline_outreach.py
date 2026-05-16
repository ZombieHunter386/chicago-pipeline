"""Tests for pipeline/outreach.py — template rendering and DB helpers."""
from __future__ import annotations
import sqlite3
from pathlib import Path
from textwrap import dedent

import pytest

from pipeline.outreach import (
    load_templates,
    render_template,
    sanitize_subject,
    upsert_contact,
    list_outreach_for_parcel,
    create_outreach_record,
    mark_replied,
    parcel_context,
    save_template,
)


# ---------- template loading + rendering ----------

def test_load_templates_returns_named_dict(tmp_path: Path) -> None:
    yaml_text = dedent("""\
        templates:
          - name: t1
            label: First
            subject: "Hi {{owner_name}}"
            body: "Body 1"
          - name: t2
            label: Second
            subject: "Hey"
            body: "Body 2"
        defaults:
          my_name: Hunter
    """)
    p = tmp_path / "templates.yaml"
    p.write_text(yaml_text)
    out = load_templates(p)
    assert set(out["templates"].keys()) == {"t1", "t2"}
    assert out["templates"]["t1"]["subject"] == "Hi {{owner_name}}"
    assert out["defaults"]["my_name"] == "Hunter"


def test_render_template_substitutes_known_variables() -> None:
    text = "Hi {{owner_first_name}}, about {{address}} (score {{score}})."
    ctx = {"owner_first_name": "Jane", "address": "123 W Main", "score": "87.4"}
    assert render_template(text, ctx) == "Hi Jane, about 123 W Main (score 87.4)."


def test_render_template_keeps_unknown_variables_literal() -> None:
    """Missing vars stay as {{name}} so the user sees what they need to fill in."""
    text = "Hi {{owner_first_name}}, your {{mystery_field}} is interesting."
    ctx = {"owner_first_name": "Jane"}
    assert render_template(text, ctx) == \
        "Hi Jane, your {{mystery_field}} is interesting."


def test_render_template_tolerates_whitespace_in_braces() -> None:
    assert render_template("{{ name }}", {"name": "Alice"}) == "Alice"


def test_render_template_handles_non_string_values() -> None:
    assert render_template("Score: {{score}}", {"score": 87.4}) == "Score: 87.4"


def test_sanitize_subject_strips_newlines_and_cr() -> None:
    assert sanitize_subject("Hello\r\nBcc: evil@example.com") == \
        "HelloBcc: evil@example.com"
    assert sanitize_subject("Plain subject") == "Plain subject"


# ---------- DB helpers ----------

@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    """Minimal schema mirroring the relevant tables. Real schema lives in
    pipeline/db.py; we duplicate the parts we touch here to keep this test
    isolated from full-schema initialization."""
    conn = sqlite3.connect(tmp_path / "t.db")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE parcels (
            pin TEXT PRIMARY KEY, owner_name TEXT, address TEXT,
            stage TEXT DEFAULT 'scored', score REAL
        );
        CREATE TABLE contacts (
            contact_id INTEGER PRIMARY KEY AUTOINCREMENT,
            pin TEXT, consolidation_group_id INTEGER,
            name TEXT, phone TEXT, email TEXT,
            mailing_address TEXT, role TEXT, source TEXT
        );
        CREATE TABLE outreach (
            outreach_id INTEGER PRIMARY KEY AUTOINCREMENT,
            wave_id INTEGER, pin TEXT, consolidation_group_id INTEGER,
            contact_id INTEGER, channel TEXT, touch_number INTEGER,
            sent_date TEXT, response_date TEXT, response_type TEXT,
            handed_off INTEGER DEFAULT 0, handed_off_date TEXT,
            draft_subject TEXT, draft_body TEXT, final_body TEXT,
            lob_tracking_id TEXT, lob_status TEXT, notes TEXT
        );
        INSERT INTO parcels (pin, owner_name, address, score)
            VALUES ('14210010010000', 'JOHN SMITH', '123 W Main St', 82.5);
    """)
    conn.commit()
    return conn


def test_upsert_contact_inserts_new(db: sqlite3.Connection) -> None:
    cid = upsert_contact(db, pin="14210010010000",
                         email="js@example.com", name="John Smith", source="manual")
    row = db.execute("SELECT * FROM contacts WHERE contact_id = ?", (cid,)).fetchone()
    assert row["email"] == "js@example.com"
    assert row["name"] == "John Smith"
    assert row["source"] == "manual"


def test_upsert_contact_updates_existing(db: sqlite3.Connection) -> None:
    cid1 = upsert_contact(db, pin="14210010010000", email="old@example.com")
    cid2 = upsert_contact(db, pin="14210010010000", email="new@example.com")
    assert cid1 == cid2  # same row, updated in place
    n = db.execute("SELECT COUNT(*) FROM contacts WHERE pin = ?",
                   ("14210010010000",)).fetchone()[0]
    assert n == 1
    row = db.execute("SELECT * FROM contacts WHERE contact_id = ?", (cid1,)).fetchone()
    assert row["email"] == "new@example.com"


def test_upsert_contact_preserves_unset_fields(db: sqlite3.Connection) -> None:
    """Upserting only the email shouldn't blank out the name."""
    upsert_contact(db, pin="14210010010000", email="x@y.com", name="John")
    upsert_contact(db, pin="14210010010000", email="x@y.com", phone="555-0100")
    row = db.execute("SELECT * FROM contacts WHERE pin = ?",
                     ("14210010010000",)).fetchone()
    assert row["name"] == "John"
    assert row["phone"] == "555-0100"


def test_create_outreach_record_inserts_and_returns_id(db: sqlite3.Connection) -> None:
    cid = upsert_contact(db, pin="14210010010000", email="js@example.com")
    oid = create_outreach_record(
        db, pin="14210010010000", contact_id=cid,
        channel="email", subject="Hi", body="Body text",
        sent_date="2026-05-14T12:34:56Z",
    )
    row = db.execute("SELECT * FROM outreach WHERE outreach_id = ?", (oid,)).fetchone()
    assert row["channel"] == "email"
    assert row["draft_subject"] == "Hi"
    assert row["final_body"] == "Body text"
    assert row["sent_date"] == "2026-05-14T12:34:56Z"
    assert row["touch_number"] == 1


def test_list_outreach_for_parcel_returns_in_reverse_chrono(db: sqlite3.Connection) -> None:
    create_outreach_record(db, pin="14210010010000", contact_id=None,
                           channel="email", subject="First", body="b1",
                           sent_date="2026-05-10T09:00:00Z")
    create_outreach_record(db, pin="14210010010000", contact_id=None,
                           channel="email", subject="Second", body="b2",
                           sent_date="2026-05-14T09:00:00Z")
    rows = list_outreach_for_parcel(db, "14210010010000")
    assert [r["draft_subject"] for r in rows] == ["Second", "First"]


def test_mark_replied_sets_response_fields(db: sqlite3.Connection) -> None:
    oid = create_outreach_record(db, pin="14210010010000", contact_id=None,
                                 channel="email", subject="Hi", body="b",
                                 sent_date="2026-05-14T09:00:00Z")
    mark_replied(db, oid, response_date="2026-05-15T11:00:00Z",
                 response_type="responded")
    row = db.execute("SELECT * FROM outreach WHERE outreach_id = ?", (oid,)).fetchone()
    assert row["response_date"] == "2026-05-15T11:00:00Z"
    assert row["response_type"] == "responded"


def test_parcel_context_builds_merge_vars(db: sqlite3.Connection) -> None:
    """parcel_context turns a parcel row into the variables that templates use."""
    parcel = dict(db.execute(
        "SELECT * FROM parcels WHERE pin = ?", ("14210010010000",)
    ).fetchone())
    defaults = {"my_name": "Hunter", "my_email": "h@example.com", "my_phone": "555"}
    ctx = parcel_context(parcel, defaults)
    assert ctx["owner_name"] == "JOHN SMITH"
    assert ctx["owner_first_name"] == "John"
    assert ctx["address"] == "123 W Main St"
    assert ctx["score"] == "82.5"
    assert ctx["my_name"] == "Hunter"
    # Missing columns are rendered as the empty string, not None or absent
    assert ctx["ward"] == ""
    assert ctx["building_sf"] == ""


def test_parcel_context_handles_llc_owner_first_name(db: sqlite3.Connection) -> None:
    """For LLC owners, owner_first_name defaults to 'there' (no real first name)."""
    db.execute("UPDATE parcels SET owner_name = ? WHERE pin = ?",
               ("123 MAIN ST LLC", "14210010010000"))
    parcel = dict(db.execute(
        "SELECT * FROM parcels WHERE pin = ?", ("14210010010000",)
    ).fetchone())
    ctx = parcel_context(parcel, {})
    assert ctx["owner_first_name"] == "there"


def test_save_template_updates_existing(tmp_path: Path) -> None:
    p = tmp_path / "templates.yaml"
    p.write_text(
        "templates:\n"
        "  - name: t1\n"
        "    label: First\n"
        "    subject: Old subject\n"
        "    body: |\n"
        "      Old body line 1\n"
        "      Old body line 2\n"
        "defaults:\n"
        "  my_name: Hunter\n"
    )
    saved = save_template(p, name="t1", subject="New subject", body="New body\nline 2\n")
    assert saved["subject"] == "New subject"
    reloaded = load_templates(p)
    assert reloaded["templates"]["t1"]["subject"] == "New subject"
    assert reloaded["templates"]["t1"]["body"].rstrip("\n") == "New body\nline 2"
    # Defaults preserved
    assert reloaded["defaults"]["my_name"] == "Hunter"


def test_save_template_creates_new(tmp_path: Path) -> None:
    p = tmp_path / "templates.yaml"
    p.write_text(
        "templates:\n"
        "  - name: t1\n"
        "    label: First\n"
        "    subject: S\n"
        "    body: B\n"
        "defaults:\n"
        "  my_name: Hunter\n"
    )
    saved = save_template(p, name="t2", subject="S2", body="B2", label="Second")
    assert saved["label"] == "Second"
    reloaded = load_templates(p)
    assert set(reloaded["templates"].keys()) == {"t1", "t2"}
    # Original untouched
    assert reloaded["templates"]["t1"]["subject"] == "S"


def test_save_template_preserves_other_templates(tmp_path: Path) -> None:
    p = tmp_path / "templates.yaml"
    p.write_text(
        "templates:\n"
        "  - name: t1\n"
        "    label: First\n"
        "    subject: S1\n"
        "    body: B1\n"
        "  - name: t2\n"
        "    label: Second\n"
        "    subject: S2\n"
        "    body: B2\n"
        "defaults:\n"
        "  my_name: Hunter\n"
    )
    save_template(p, name="t1", subject="New S1", body="New B1")
    reloaded = load_templates(p)
    assert reloaded["templates"]["t1"]["subject"] == "New S1"
    # t2 still intact
    assert reloaded["templates"]["t2"]["subject"] == "S2"


def test_save_template_writes_multiline_as_block_scalar(tmp_path: Path) -> None:
    """Sanity check on the YAML output style — multi-line bodies should be
    written as `|` block scalars, not quoted strings, so the file stays
    readable when a human opens it."""
    p = tmp_path / "templates.yaml"
    p.write_text("templates: []\ndefaults: {}\n")
    save_template(p, name="t1", subject="s", body="line1\nline2\nline3\n")
    text = p.read_text()
    assert "body: |" in text, f"expected block scalar for body, got:\n{text}"


def test_save_template_preserves_comment_header(tmp_path: Path) -> None:
    """The standard comment header should be present after a save (PyYAML
    strips comments on round-trip; we rebuild it deterministically)."""
    p = tmp_path / "templates.yaml"
    p.write_text("templates: []\ndefaults: {}\n")
    save_template(p, name="t1", subject="s", body="b")
    text = p.read_text()
    assert text.startswith("# Email templates used by the outreach compose modal.")


def test_save_template_atomic_no_partial_on_dir_failure(tmp_path: Path, monkeypatch) -> None:
    """If os.replace fails, the source file must be untouched and no temp
    file should be left behind."""
    p = tmp_path / "templates.yaml"
    p.write_text("templates: []\ndefaults: {}\n")
    original = p.read_text()

    def boom(*args, **kwargs):
        raise OSError("simulated replace failure")
    monkeypatch.setattr("pipeline.outreach.os.replace", boom)

    with pytest.raises(OSError):
        save_template(p, name="t1", subject="s", body="b")

    assert p.read_text() == original
    # No leftover .outreach_templates.*.tmp files in the dir
    leftovers = [f for f in p.parent.iterdir() if f.name.startswith(".outreach_templates.")]
    assert leftovers == [], f"temp files left behind: {leftovers}"


def test_save_template_indents_sequence_items(tmp_path: Path) -> None:
    """Sequence items (template entries) should be indented under their
    parent key — `templates:\\n  - name: ...` not `templates:\\n- name: ...`.
    PyYAML's default is the latter, which produces ugly diffs every time the
    UI saves. The custom Dumper's increase_indent override forces the former."""
    p = tmp_path / "templates.yaml"
    p.write_text("templates: []\ndefaults: {}\n")
    save_template(p, name="t1", subject="s", body="b")
    text = p.read_text()
    assert "\n  - name: t1" in text, f"expected indented seq, got:\n{text}"


# ---------- validate_next_due_touch ----------

from pipeline.outreach import validate_next_due_touch


def test_validate_next_due_touch_accepts_touch_1_when_no_history():
    """Sending touch 1 is valid when the parcel has no prior outreach rows."""
    validate_next_due_touch(outreach_rows=[], touch_number=1)


def test_validate_next_due_touch_rejects_skip_ahead():
    """Sending touch 3 when touch 2 hasn't been done is invalid."""
    rows = [{"touch_number": 1, "sent_date": "2026-05-08T09:00:00Z"}]
    with pytest.raises(ValueError, match="next-due"):
        validate_next_due_touch(outreach_rows=rows, touch_number=3)


def test_validate_next_due_touch_rejects_already_done():
    """Sending touch 1 again is invalid."""
    rows = [{"touch_number": 1, "sent_date": "2026-05-08T09:00:00Z"}]
    with pytest.raises(ValueError, match="already"):
        validate_next_due_touch(outreach_rows=rows, touch_number=1)


def test_validate_next_due_touch_accepts_in_order():
    """After touch 1, touch 2 is the next-due touch."""
    rows = [{"touch_number": 1, "sent_date": "2026-05-08T09:00:00Z"}]
    validate_next_due_touch(outreach_rows=rows, touch_number=2)
