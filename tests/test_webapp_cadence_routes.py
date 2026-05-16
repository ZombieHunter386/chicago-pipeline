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
