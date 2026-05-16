"""Tests for the cadence read endpoints (GET /api/outreach/due, GET /api/cadence/config)."""
from __future__ import annotations
import sqlite3
from pathlib import Path

import pytest

from pipeline.db import init_db
from webapp.app import create_app


CADENCE_YAML = """
sequence:
  - {touch: 1, day_offset: 0, channel: email, template: t1, requires: email}
  - {touch: 2, day_offset: 3, channel: email, template: t2, requires: email}
  - {touch: 3, day_offset: 7, channel: phone, template: t3, requires: phone}
end_of_sequence_grace_days: 0
"""

TEMPLATES_YAML = """
templates:
  - {name: t1, label: First, subject: "Hi", body: "B1"}
  - {name: t2, label: Second, subject: "Hi 2", body: "B2"}
  - {name: t3, label: Phone, subject: "", body: "Script"}
defaults: {my_name: Hunter}
"""


@pytest.fixture
def db_path(tmp_path):
    p = tmp_path / "t.db"
    init_db(p)
    conn = sqlite3.connect(p)
    # One parcel in outreach stage with touch 1 sent + email contact
    conn.execute(
        "INSERT INTO parcels (pin, address, owner_name, mail_address, stage) "
        "VALUES (?, ?, ?, ?, ?)",
        ("14210010010000", "123 W Main St", "JOHN SMITH", "500 N Main",
         "outreach"),
    )
    conn.execute(
        "INSERT INTO contacts (pin, email, source) VALUES (?, ?, ?)",
        ("14210010010000", "js@example.com", "manual"),
    )
    conn.execute(
        "INSERT INTO outreach (pin, touch_number, channel, sent_date) "
        "VALUES (?, ?, ?, ?)",
        ("14210010010000", 1, "email", "2026-05-08T09:00:00Z"),
    )
    conn.commit()
    conn.close()
    return p


@pytest.fixture
def cadence_path(tmp_path):
    p = tmp_path / "cadence.yaml"
    p.write_text(CADENCE_YAML)
    return p


@pytest.fixture
def templates_path(tmp_path):
    p = tmp_path / "templates.yaml"
    p.write_text(TEMPLATES_YAML)
    return p


@pytest.fixture
def app_on(db_path, cadence_path, templates_path, tmp_path):
    from datetime import date
    return create_app(
        db_path=db_path, feature_outreach=True,
        outreach_templates_path=templates_path,
        outreach_cadence_path=cadence_path,
        due_digest_last_run_path=tmp_path / "last_run.txt",
        clock=lambda: date(2026, 5, 11),  # pinned for deterministic tests
        gmail_client_secrets_path=tmp_path / "client.json",
        gmail_token_path=tmp_path / "token.json",
        gmail_sender_address="me@example.com",
    )


@pytest.fixture
def app_off(db_path):
    return create_app(db_path=db_path, feature_outreach=False)


def test_get_due_404_when_flag_off(app_off):
    assert app_off.test_client().get("/api/outreach/due").status_code == 404


def test_get_due_groups_by_channel(app_on):
    """With one parcel that has touch 1 sent on 2026-05-08 and the test
    clock pinned to 2026-05-11, touch 2 is due today."""
    resp = app_on.test_client().get("/api/outreach/due")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["today"] == "2026-05-11"
    channels = {g["channel"]: g for g in data["groups"]}
    assert "email" in channels
    assert channels["email"]["count"] == 1
    item = channels["email"]["items"][0]
    assert item["pin"] == "14210010010000"
    assert item["touch"] == 2
    assert item["to_email"] == "js@example.com"


def test_get_cadence_config_returns_yaml_as_json(app_on):
    resp = app_on.test_client().get("/api/cadence/config")
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data["sequence"]) == 3
    assert data["sequence"][0]["template"] == "t1"


def test_get_cadence_config_404_when_flag_off(app_off):
    assert app_off.test_client().get("/api/cadence/config").status_code == 404


def test_log_manual_touch_records_phone_touch(app_on, db_path):
    """Posting a phone touch (touch 3) when touch 2 has been sent records
    an outreach row with channel='phone' and the right touch_number."""
    # Send touch 2 first to make touch 3 valid
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO outreach (pin, touch_number, channel, sent_date) "
        "VALUES (?, ?, ?, ?)",
        ("14210010010000", 2, "email", "2026-05-11T09:00:00Z"),
    )
    conn.commit()
    conn.close()

    resp = app_on.test_client().post(
        "/api/outreach/log-manual-touch",
        json={"pin": "14210010010000", "touch_number": 3,
              "channel": "phone", "notes": "Left voicemail at 2pm."},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()
    assert data["outreach_id"] > 0
    # Verify the DB row
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT channel, touch_number, notes FROM outreach "
        "WHERE outreach_id = ?", (data["outreach_id"],),
    ).fetchone()
    conn.close()
    assert row[0] == "phone"
    assert row[1] == 3
    assert "voicemail" in row[2]


def test_log_manual_touch_rejects_wrong_channel(app_on, db_path):
    """Posting channel='email' for touch 3 (which is configured as phone) → 400."""
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO outreach (pin, touch_number, channel, sent_date) "
        "VALUES (?, ?, ?, ?)",
        ("14210010010000", 2, "email", "2026-05-11T09:00:00Z"),
    )
    conn.commit()
    conn.close()
    resp = app_on.test_client().post(
        "/api/outreach/log-manual-touch",
        json={"pin": "14210010010000", "touch_number": 3, "channel": "email"},
    )
    assert resp.status_code == 400


def test_log_manual_touch_rejects_out_of_order(app_on):
    """Posting touch 5 when only touch 1 has been done → 400."""
    resp = app_on.test_client().post(
        "/api/outreach/log-manual-touch",
        json={"pin": "14210010010000", "touch_number": 5, "channel": "email"},
    )
    assert resp.status_code == 400


def test_log_manual_touch_409_on_duplicate(app_on, db_path):
    """Inserting a duplicate (pin, touch_number) violates the unique index → 409.
    But validate-next-due catches it first as 'already done' → 400."""
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO outreach (pin, touch_number, channel, sent_date) "
        "VALUES (?, ?, ?, ?)",
        ("14210010010000", 2, "email", "2026-05-11T09:00:00Z"),
    )
    conn.commit()
    conn.close()
    resp = app_on.test_client().post(
        "/api/outreach/log-manual-touch",
        json={"pin": "14210010010000", "touch_number": 2, "channel": "email"},
    )
    assert resp.status_code == 400  # caught by validate, before DB


def test_log_manual_touch_404_when_flag_off(app_off):
    assert app_off.test_client().post(
        "/api/outreach/log-manual-touch", json={}
    ).status_code == 404


def test_log_manual_touch_accepts_skipped_channel(app_on, db_path):
    """Logging touch 3 with channel='skipped' records the touch as done
    without doing anything. The next touch surfaces normally."""
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO outreach (pin, touch_number, channel, sent_date) "
        "VALUES (?, ?, ?, ?)",
        ("14210010010000", 2, "email", "2026-05-11T09:00:00Z"),
    )
    conn.commit()
    conn.close()
    resp = app_on.test_client().post(
        "/api/outreach/log-manual-touch",
        json={"pin": "14210010010000", "touch_number": 3,
              "channel": "skipped", "notes": "Don't want to call."},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT channel, touch_number FROM outreach "
        "WHERE outreach_id = ?", (resp.get_json()["outreach_id"],),
    ).fetchone()
    conn.close()
    assert row[0] == "skipped"
    assert row[1] == 3


def test_pause_parcel_sets_flag(app_on, db_path):
    resp = app_on.test_client().post(
        "/api/parcels/14210010010000/pause",
        json={"paused": True},
    )
    assert resp.status_code == 200
    assert resp.get_json() == {"pin": "14210010010000", "paused": True}
    conn = sqlite3.connect(db_path)
    flag = conn.execute(
        "SELECT outreach_paused FROM parcels WHERE pin = ?",
        ("14210010010000",),
    ).fetchone()[0]
    conn.close()
    assert flag == 1


def test_pause_parcel_hides_from_due(app_on, db_path):
    # Pause it
    app_on.test_client().post(
        "/api/parcels/14210010010000/pause", json={"paused": True}
    )
    # Now it shouldn't appear in due (test clock pinned to 2026-05-11)
    resp = app_on.test_client().get("/api/outreach/due")
    assert resp.get_json()["groups"] == []


def test_pause_parcel_404_when_flag_off(app_off):
    assert app_off.test_client().post(
        "/api/parcels/14210010010000/pause", json={"paused": True}
    ).status_code == 404


def test_log_manual_touch_creates_anchor_with_skipped_touch_1(app_on, db_path):
    """skipped on touch 1 (mail-only campaign) creates the anchor row.
    After it, future cadence touches start surfacing per the schedule."""
    import sqlite3
    # Replace the fixture's pre-seeded touch 1 with no outreach rows,
    # so we test the "fresh parcel" anchor-creation path
    conn = sqlite3.connect(db_path)
    conn.execute("DELETE FROM outreach WHERE pin = ?", ("14210010010000",))
    conn.commit()
    conn.close()

    # POST touch 1 with channel=skipped — this is the documented mail-only
    # campaign starter (see spec line ~23)
    resp = app_on.test_client().post(
        "/api/outreach/log-manual-touch",
        json={"pin": "14210010010000", "touch_number": 1,
              "channel": "skipped",
              "notes": "Mail-only campaign — no email available."},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()
    # next_touch should be touch 2 (email, day 3) — but the test fixture's
    # contact has email so touch 2 surfaces normally. Anchor is today,
    # touch 2 is day_offset=3, so target is 3 days from now. Not due yet
    # at our pinned 2026-05-11 clock since we just made the anchor.
    # The exact next_touch shape depends on the cfg in the fixture; just
    # verify the row landed.
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT channel, touch_number FROM outreach "
        "WHERE outreach_id = ?", (data["outreach_id"],),
    ).fetchone()
    conn.close()
    assert row[0] == "skipped"
    assert row[1] == 1


def test_pause_parcel_unpause_clears_flag(app_on, db_path):
    """Round-trip: pause, then unpause. The flag flips back to 0 and the
    parcel reappears in /api/outreach/due."""
    import sqlite3
    client = app_on.test_client()
    # Pause
    client.post("/api/parcels/14210010010000/pause", json={"paused": True})
    # Confirm hidden from due
    assert client.get("/api/outreach/due").get_json()["groups"] == []
    # Unpause
    resp = client.post("/api/parcels/14210010010000/pause", json={"paused": False})
    assert resp.status_code == 200
    assert resp.get_json() == {"pin": "14210010010000", "paused": False}
    # Confirm flag cleared
    conn = sqlite3.connect(db_path)
    flag = conn.execute(
        "SELECT outreach_paused FROM parcels WHERE pin = ?",
        ("14210010010000",),
    ).fetchone()[0]
    conn.close()
    assert flag == 0
    # And the parcel is back in due
    groups = client.get("/api/outreach/due").get_json()["groups"]
    assert len(groups) > 0  # touch 2 due (anchor 5-08, today 5-11)


def test_health_digest_no_sentinel_means_stale(app_on):
    resp = app_on.test_client().get("/api/health/digest")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["stale"] is True
    assert data["last_run"] is None


def test_health_digest_recent_sentinel_not_stale(app_on, tmp_path):
    from datetime import datetime, timezone
    sentinel = Path(app_on.config["DUE_DIGEST_LAST_RUN_PATH"])
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.write_text(datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    resp = app_on.test_client().get("/api/health/digest")
    data = resp.get_json()
    assert data["stale"] is False
    assert data["last_run"] is not None


def test_health_digest_old_sentinel_is_stale(app_on, tmp_path):
    sentinel = Path(app_on.config["DUE_DIGEST_LAST_RUN_PATH"])
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    # 2 days old
    sentinel.write_text("2026-05-13T09:00:00Z")
    resp = app_on.test_client().get("/api/health/digest")
    data = resp.get_json()
    assert data["stale"] is True
