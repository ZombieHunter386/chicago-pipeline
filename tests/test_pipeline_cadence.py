"""Tests for pipeline/cadence.py — pure cadence engine functions."""
from __future__ import annotations
from datetime import date
from pathlib import Path

import pytest
import sqlite3

from pipeline.cadence import (
    load_cadence_config,
    next_due_touches_for_parcel,
    is_end_of_sequence,
    all_due_touches,
)


# ---------- load_cadence_config ----------

def _write_config(path: Path, sequence_yaml: str) -> Path:
    path.write_text(sequence_yaml)
    return path


def test_load_cadence_config_parses_minimal(tmp_path):
    p = _write_config(tmp_path / "c.yaml", """
sequence:
  - touch: 1
    day_offset: 0
    channel: email
    template: tpl-1
    requires: email
""")
    cfg = load_cadence_config(p)
    assert len(cfg["sequence"]) == 1
    assert cfg["sequence"][0]["touch"] == 1
    assert cfg["end_of_sequence_grace_days"] == 0


def test_load_cadence_config_sorts_by_touch(tmp_path):
    p = _write_config(tmp_path / "c.yaml", """
sequence:
  - {touch: 2, day_offset: 3, channel: email, template: t2, requires: email}
  - {touch: 1, day_offset: 0, channel: email, template: t1, requires: email}
""")
    cfg = load_cadence_config(p)
    assert [t["touch"] for t in cfg["sequence"]] == [1, 2]


def test_load_cadence_config_rejects_empty_sequence(tmp_path):
    p = _write_config(tmp_path / "c.yaml", "sequence: []\n")
    with pytest.raises(ValueError, match="non-empty"):
        load_cadence_config(p)


def test_load_cadence_config_rejects_unknown_channel(tmp_path):
    p = _write_config(tmp_path / "c.yaml", """
sequence:
  - {touch: 1, day_offset: 0, channel: smoke_signal, template: t, requires: email}
""")
    with pytest.raises(ValueError, match="channel"):
        load_cadence_config(p)


def test_load_cadence_config_rejects_unknown_requires(tmp_path):
    p = _write_config(tmp_path / "c.yaml", """
sequence:
  - {touch: 1, day_offset: 0, channel: email, template: t, requires: fax}
""")
    with pytest.raises(ValueError, match="requires"):
        load_cadence_config(p)


def test_load_cadence_config_rejects_missing_field(tmp_path):
    p = _write_config(tmp_path / "c.yaml", """
sequence:
  - {touch: 1, day_offset: 0, channel: email, requires: email}
""")
    with pytest.raises(ValueError, match="template"):
        load_cadence_config(p)


# ---------- next_due_touches_for_parcel ----------

# A standard 3-touch fixture for the engine tests.
@pytest.fixture
def cfg():
    return {
        "sequence": [
            {"touch": 1, "day_offset": 0, "channel": "email",
             "template": "t1", "requires": "email"},
            {"touch": 2, "day_offset": 3, "channel": "email",
             "template": "t2", "requires": "email"},
            {"touch": 3, "day_offset": 7, "channel": "phone",
             "template": "t3", "requires": "phone"},
        ],
        "end_of_sequence_action": "surface_for_dead",
        "end_of_sequence_grace_days": 0,
    }


def test_no_touch_1_means_no_due_touches(cfg):
    """A parcel with no touch_1 row hasn't entered cadence — empty result."""
    out = next_due_touches_for_parcel(
        cadence_config=cfg, outreach_rows=[],
        contact={"email": "a@b.com"},
        parcel_mail_address="100 Main St",
        today=date(2026, 5, 15),
    )
    assert out == []


def test_touch_2_due_3_days_after_touch_1(cfg):
    rows = [{"touch_number": 1, "sent_date": "2026-05-12T09:00:00Z"}]
    out = next_due_touches_for_parcel(
        cadence_config=cfg, outreach_rows=rows,
        contact={"email": "a@b.com"},
        parcel_mail_address="100 Main St",
        today=date(2026, 5, 15),  # exactly day 3
    )
    assert len(out) == 1
    assert out[0]["touch"] == 2
    assert out[0]["target_date"] == "2026-05-15"
    assert out[0]["days_overdue"] == 0


def test_overdue_touch_includes_days_overdue_count(cfg):
    rows = [{"touch_number": 1, "sent_date": "2026-05-08T09:00:00Z"}]
    out = next_due_touches_for_parcel(
        cadence_config=cfg, outreach_rows=rows,
        contact={"email": "a@b.com", "phone": "555-0100"},
        parcel_mail_address="100 Main St",
        today=date(2026, 5, 16),  # touch 2 was due 5-11, touch 3 was due 5-15
    )
    touches = {t["touch"]: t for t in out}
    assert 2 in touches and 3 in touches
    assert touches[2]["days_overdue"] == 5
    assert touches[3]["days_overdue"] == 1


def test_touch_3_skipped_when_no_phone(cfg):
    rows = [{"touch_number": 1, "sent_date": "2026-05-08T09:00:00Z"}]
    out = next_due_touches_for_parcel(
        cadence_config=cfg, outreach_rows=rows,
        contact={"email": "a@b.com"},  # no phone
        parcel_mail_address="100 Main St",
        today=date(2026, 5, 16),
    )
    touches = {t["touch"] for t in out}
    assert touches == {2}  # touch 3 silently skipped


def test_completed_touch_doesnt_resurface(cfg):
    rows = [
        {"touch_number": 1, "sent_date": "2026-05-08T09:00:00Z"},
        {"touch_number": 2, "sent_date": "2026-05-12T09:00:00Z"},
    ]
    out = next_due_touches_for_parcel(
        cadence_config=cfg, outreach_rows=rows,
        contact={"email": "a@b.com", "phone": "555-0100"},
        parcel_mail_address="100 Main St",
        today=date(2026, 5, 16),
    )
    touches = {t["touch"] for t in out}
    assert touches == {3}  # touch 2 already done


def test_future_touch_not_yet_due(cfg):
    rows = [{"touch_number": 1, "sent_date": "2026-05-14T09:00:00Z"}]
    out = next_due_touches_for_parcel(
        cadence_config=cfg, outreach_rows=rows,
        contact={"email": "a@b.com", "phone": "555-0100"},
        parcel_mail_address="100 Main St",
        today=date(2026, 5, 15),  # only day 1, touch 2 is day 3
    )
    assert out == []


def test_target_dates_anchored_to_touch_1_not_shifted_by_late_touches(cfg):
    """Even if touch 2 was sent late, touch 3's target is still touch_1 + 7."""
    rows = [
        {"touch_number": 1, "sent_date": "2026-05-01T09:00:00Z"},
        {"touch_number": 2, "sent_date": "2026-05-10T09:00:00Z"},  # 9 days late
    ]
    out = next_due_touches_for_parcel(
        cadence_config=cfg, outreach_rows=rows,
        contact={"email": "a@b.com", "phone": "555-0100"},
        parcel_mail_address="100 Main St",
        today=date(2026, 5, 12),
    )
    # touch 3 target_date should be 2026-05-08 (anchor + 7 days), NOT 2026-05-17
    assert len(out) == 1
    assert out[0]["touch"] == 3
    assert out[0]["target_date"] == "2026-05-08"


def test_outreach_rows_with_null_touch_number_are_ignored(cfg):
    """Pre-cadence outreach rows have touch_number=None. They must not
    interfere with the anchor lookup or the by_touch dict."""
    rows = [
        {"touch_number": None, "sent_date": "2026-05-01T09:00:00Z"},  # pre-cadence row
        {"touch_number": 1, "sent_date": "2026-05-08T09:00:00Z"},
    ]
    out = next_due_touches_for_parcel(
        cadence_config=cfg, outreach_rows=rows,
        contact={"email": "a@b.com"},
        parcel_mail_address="100 Main St",
        today=date(2026, 5, 12),
    )
    # Touch 2 should be due (anchor = touch_1 = 5-08, day 3 = 5-11, today 5-12)
    assert len(out) == 1
    assert out[0]["touch"] == 2


# ---------- is_end_of_sequence ----------

def test_end_of_sequence_false_when_touches_incomplete(cfg):
    rows = [
        {"touch_number": 1, "sent_date": "2026-04-15T09:00:00Z"},
        {"touch_number": 2, "sent_date": "2026-04-18T09:00:00Z"},
    ]
    assert is_end_of_sequence(
        cadence_config=cfg, outreach_rows=rows, today=date(2026, 5, 15),
    ) is False


def test_end_of_sequence_true_when_all_done_past_grace(cfg):
    rows = [
        {"touch_number": 1, "sent_date": "2026-04-15T09:00:00Z"},
        {"touch_number": 2, "sent_date": "2026-04-18T09:00:00Z"},
        {"touch_number": 3, "sent_date": "2026-04-22T09:00:00Z"},
    ]
    assert is_end_of_sequence(
        cadence_config=cfg, outreach_rows=rows, today=date(2026, 5, 15),
    ) is True


def test_end_of_sequence_respects_grace_days(cfg):
    cfg = {**cfg, "end_of_sequence_grace_days": 30}
    rows = [
        {"touch_number": 1, "sent_date": "2026-04-15T09:00:00Z"},
        {"touch_number": 2, "sent_date": "2026-04-18T09:00:00Z"},
        {"touch_number": 3, "sent_date": "2026-05-10T09:00:00Z"},  # 5 days ago
    ]
    assert is_end_of_sequence(
        cadence_config=cfg, outreach_rows=rows, today=date(2026, 5, 15),
    ) is False  # only 5 days since last, need 30


def test_end_of_sequence_false_when_last_touch_has_no_sent_date(cfg):
    """A touch_number row without sent_date can't anchor end-of-sequence —
    happens transiently for manual touches mid-write."""
    rows = [
        {"touch_number": 1, "sent_date": "2026-04-15T09:00:00Z"},
        {"touch_number": 2, "sent_date": "2026-04-18T09:00:00Z"},
        {"touch_number": 3, "sent_date": None},
    ]
    assert is_end_of_sequence(
        cadence_config=cfg, outreach_rows=rows, today=date(2026, 5, 15),
    ) is False


# ---------- all_due_touches (DB orchestrator) ----------


@pytest.fixture
def db(tmp_path):
    """Minimal schema mirroring parcels/contacts/outreach for orchestrator
    tests. Real schema lives in pipeline/db.py; we duplicate the parts we
    touch here to keep these tests isolated from full init_db."""
    conn = sqlite3.connect(tmp_path / "t.db")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE parcels (
            pin TEXT PRIMARY KEY, address TEXT, owner_name TEXT,
            mail_address TEXT, score REAL, stage TEXT DEFAULT 'scored',
            outreach_paused INTEGER DEFAULT 0
        );
        CREATE TABLE contacts (
            contact_id INTEGER PRIMARY KEY AUTOINCREMENT,
            pin TEXT, name TEXT, phone TEXT, email TEXT,
            mailing_address TEXT, role TEXT, source TEXT
        );
        CREATE TABLE outreach (
            outreach_id INTEGER PRIMARY KEY AUTOINCREMENT,
            wave_id INTEGER, pin TEXT, contact_id INTEGER, channel TEXT,
            touch_number INTEGER, sent_date TEXT, response_date TEXT,
            response_type TEXT, draft_subject TEXT, draft_body TEXT,
            final_body TEXT, notes TEXT
        );
    """)
    conn.commit()
    return conn


def test_all_due_touches_empty_when_no_outreach_parcels(db, cfg):
    db.execute(
        "INSERT INTO parcels (pin, stage) VALUES (?, ?)",
        ("14210010010000", "scored"),
    )
    db.commit()
    out = all_due_touches(db, cfg, date(2026, 5, 15))
    assert out["today"] == "2026-05-15"
    assert out["groups"] == []


def test_all_due_touches_groups_by_channel(db, cfg):
    """Two parcels in outreach stage: parcel A has touch 2 due (email),
    parcel B has touch 3 due (phone). Result has two groups, one per channel."""
    db.executescript("""
        INSERT INTO parcels (pin, address, owner_name, mail_address, stage)
        VALUES ('14210010010000', '123 W Main', 'JANE DOE', '500 N Main', 'outreach');
        INSERT INTO parcels (pin, address, owner_name, mail_address, stage)
        VALUES ('14210010020000', '456 W Halsted', 'JOHN ROE', '600 W Halsted', 'outreach');
        INSERT INTO contacts (pin, email, phone) VALUES ('14210010010000', 'jane@example.com', NULL);
        INSERT INTO contacts (pin, email, phone) VALUES ('14210010020000', 'john@example.com', '555-0123');
        INSERT INTO outreach (pin, touch_number, channel, sent_date)
        VALUES ('14210010010000', 1, 'email', '2026-05-08T09:00:00Z');
        INSERT INTO outreach (pin, touch_number, channel, sent_date)
        VALUES ('14210010020000', 1, 'email', '2026-05-01T09:00:00Z');
        INSERT INTO outreach (pin, touch_number, channel, sent_date)
        VALUES ('14210010020000', 2, 'email', '2026-05-05T09:00:00Z');
    """)
    db.commit()
    out = all_due_touches(db, cfg, date(2026, 5, 11))
    channels = {g["channel"]: g for g in out["groups"]}
    # A: touch 2 (email) due 5-11 (anchor 5-8 + 3 days)
    # B: touch 3 (phone) due 5-8 (anchor 5-1 + 7 days), overdue 3 days
    assert "email" in channels
    assert "phone" in channels
    assert channels["email"]["count"] == 1
    assert channels["email"]["items"][0]["pin"] == "14210010010000"
    assert channels["phone"]["count"] == 1
    assert channels["phone"]["items"][0]["pin"] == "14210010020000"
    assert channels["phone"]["items"][0]["days_overdue"] == 3


def test_all_due_touches_skips_paused(db, cfg):
    db.executescript("""
        INSERT INTO parcels (pin, address, mail_address, stage, outreach_paused)
        VALUES ('14210010010000', '123 W Main', '500 N Main', 'outreach', 1);
        INSERT INTO contacts (pin, email) VALUES ('14210010010000', 'a@b.com');
        INSERT INTO outreach (pin, touch_number, channel, sent_date)
        VALUES ('14210010010000', 1, 'email', '2026-05-08T09:00:00Z');
    """)
    db.commit()
    out = all_due_touches(db, cfg, date(2026, 5, 12))
    assert out["groups"] == []


def test_all_due_touches_skips_responded(db, cfg):
    db.executescript("""
        INSERT INTO parcels (pin, address, mail_address, stage)
        VALUES ('14210010010000', '123 W Main', '500 N Main', 'responded');
        INSERT INTO contacts (pin, email) VALUES ('14210010010000', 'a@b.com');
        INSERT INTO outreach (pin, touch_number, channel, sent_date)
        VALUES ('14210010010000', 1, 'email', '2026-05-08T09:00:00Z');
    """)
    db.commit()
    out = all_due_touches(db, cfg, date(2026, 5, 12))
    assert out["groups"] == []


def test_all_due_touches_surfaces_end_of_sequence(db, cfg):
    """Parcel with all 3 touches completed (cfg has 3 touches) → appears in
    end_of_sequence group with suggest=mark_dead."""
    db.executescript("""
        INSERT INTO parcels (pin, address, mail_address, stage)
        VALUES ('14210010010000', '123 W Main', '500 N Main', 'outreach');
        INSERT INTO contacts (pin, email, phone) VALUES ('14210010010000', 'a@b.com', '555-0100');
        INSERT INTO outreach (pin, touch_number, channel, sent_date) VALUES
            ('14210010010000', 1, 'email', '2026-04-01T09:00:00Z'),
            ('14210010010000', 2, 'email', '2026-04-04T09:00:00Z'),
            ('14210010010000', 3, 'phone', '2026-04-08T09:00:00Z');
    """)
    db.commit()
    out = all_due_touches(db, cfg, date(2026, 5, 15))
    channels = {g["channel"]: g for g in out["groups"]}
    assert "end_of_sequence" in channels
    item = channels["end_of_sequence"]["items"][0]
    assert item["pin"] == "14210010010000"
    assert item["suggest"] == "mark_dead"
    assert item["days_since_last"] > 30


def test_all_due_touches_includes_channel_and_to_address_on_each_item(db, cfg):
    """Items are self-describing: every item has a `channel` key, email items
    carry to_email, phone items carry to_phone, and (when applicable in a
    fuller cfg) mail items carry to_mail_address."""
    db.executescript("""
        INSERT INTO parcels (pin, address, owner_name, mail_address, stage)
        VALUES ('14210010010000', '123 W Main', 'JANE DOE', '500 N Main', 'outreach');
        INSERT INTO contacts (pin, email, phone) VALUES ('14210010010000', 'j@b.com', NULL);
        INSERT INTO outreach (pin, touch_number, channel, sent_date)
        VALUES ('14210010010000', 1, 'email', '2026-05-08T09:00:00Z');
    """)
    db.commit()
    out = all_due_touches(db, cfg, date(2026, 5, 11))
    email_items = next(g["items"] for g in out["groups"] if g["channel"] == "email")
    assert email_items[0]["channel"] == "email"
    assert email_items[0]["to_email"] == "j@b.com"


def test_all_due_touches_mail_items_carry_mail_address(db, tmp_path):
    """In a cadence with a mail touch, items get to_mail_address populated
    from the parcel's mail_address. The standard `cfg` fixture has no mail
    touches, so build a one-touch mail cadence inline."""
    mail_cfg = {
        "sequence": [
            {"touch": 1, "day_offset": 0, "channel": "email",
             "template": "t1", "requires": "email"},
            {"touch": 2, "day_offset": 7, "channel": "mail",
             "template": "letter", "requires": "mail_address"},
        ],
        "end_of_sequence_action": "surface_for_dead",
        "end_of_sequence_grace_days": 0,
    }
    db.executescript("""
        INSERT INTO parcels (pin, address, owner_name, mail_address, stage)
        VALUES ('14210010010000', '123 W Main', 'JANE DOE', '999 PO Box', 'outreach');
        INSERT INTO contacts (pin, email) VALUES ('14210010010000', 'j@b.com');
        INSERT INTO outreach (pin, touch_number, channel, sent_date)
        VALUES ('14210010010000', 1, 'email', '2026-05-01T09:00:00Z');
    """)
    db.commit()
    out = all_due_touches(db, mail_cfg, date(2026, 5, 9))  # touch 2 due day 7+
    mail_items = next(g["items"] for g in out["groups"] if g["channel"] == "mail")
    assert mail_items[0]["channel"] == "mail"
    assert mail_items[0]["to_mail_address"] == "999 PO Box"
