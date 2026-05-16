"""Tests for pipeline/cadence.py — pure cadence engine functions."""
from __future__ import annotations
from datetime import date
from pathlib import Path

import pytest

from pipeline.cadence import (
    load_cadence_config,
    next_due_touches_for_parcel,
    is_end_of_sequence,
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
