"""Tests for the outreach read/write endpoints.

Routes only exist when FEATURE_OUTREACH is true (Railway runs with it off,
so these endpoints return 404 in prod). Gmail API is mocked end-to-end.
"""
from __future__ import annotations
import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from pipeline.db import init_db
from webapp.app import create_app


@pytest.fixture
def outreach_db_path(tmp_path: Path) -> Path:
    """A fresh DB with the full schema and one seeded parcel. Named to avoid
    shadowing the global db_path fixture in tests/conftest.py, which seeds
    nothing."""
    path = tmp_path / "outreach.db"
    init_db(path)
    conn = sqlite3.connect(path)
    conn.execute(
        "INSERT INTO parcels (pin, owner_name, address, score, stage) "
        "VALUES (?, ?, ?, ?, ?)",
        ("14210010010000", "JOHN SMITH", "123 W Main St", 82.5, "scored"),
    )
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def templates_path(tmp_path: Path) -> Path:
    p = tmp_path / "templates.yaml"
    p.write_text(
        "templates:\n"
        "  - name: t1\n"
        "    label: First\n"
        "    subject: \"Hi {{owner_first_name}}\"\n"
        "    body: \"About {{address}}\"\n"
        "defaults:\n"
        "  my_name: Hunter\n"
    )
    return p


@pytest.fixture
def cadence_path_in_outreach_tests(tmp_path: Path) -> Path:
    p = tmp_path / "cadence.yaml"
    p.write_text("""
sequence:
  - {touch: 1, day_offset: 0, channel: email, template: t1, requires: email}
  - {touch: 2, day_offset: 3, channel: email, template: t1, requires: email}
  - {touch: 5, day_offset: 19, channel: email, template: t1, requires: email}
end_of_sequence_grace_days: 0
""")
    return p


@pytest.fixture
def app_on(outreach_db_path: Path, templates_path: Path,
           cadence_path_in_outreach_tests: Path, tmp_path: Path):
    from datetime import date
    return create_app(
        db_path=outreach_db_path, feature_outreach=True,
        outreach_templates_path=templates_path,
        outreach_cadence_path=cadence_path_in_outreach_tests,
        clock=lambda: date(2026, 5, 11),  # pinned for deterministic tests
        gmail_client_secrets_path=tmp_path / "client.json",
        gmail_token_path=tmp_path / "token.json",
        gmail_sender_address="me@example.com",
    )


@pytest.fixture
def app_off(outreach_db_path: Path):
    return create_app(db_path=outreach_db_path, feature_outreach=False)


# ---------- feature flag gates the routes entirely ----------

def test_outreach_routes_return_404_when_flag_off(app_off) -> None:
    """All 8 outreach routes must be unreachable when the feature flag is off.
    Railway runs with FEATURE_OUTREACH unset, so these endpoints don't exist
    in prod — this test pins that behavior."""
    client = app_off.test_client()
    assert client.get("/api/parcels/14210010010000/outreach").status_code == 404
    assert client.get("/api/outreach/templates").status_code == 404
    assert client.post("/api/outreach/templates/save").status_code == 404
    assert client.post("/api/contacts/upsert").status_code == 404
    assert client.post("/api/outreach/send").status_code == 404
    assert client.post("/api/outreach/1/mark-replied").status_code == 404
    assert client.post("/api/parcels/14210010010000/stage").status_code == 404
    assert client.get("/api/oauth/start").status_code == 404
    assert client.get("/api/oauth/callback").status_code == 404


# ---------- GET /api/parcels/<pin>/outreach ----------

def test_get_outreach_returns_empty_lists_for_new_parcel(app_on) -> None:
    client = app_on.test_client()
    resp = client.get("/api/parcels/14210010010000/outreach")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["pin"] == "14210010010000"
    assert data["contact"] is None
    assert data["outreach"] == []
    assert data["gmail_connected"] is False


def test_get_outreach_returns_404_for_unknown_pin(app_on) -> None:
    client = app_on.test_client()
    assert client.get("/api/parcels/99999999999999/outreach").status_code == 404


# ---------- POST /api/contacts/upsert ----------

def test_upsert_contact_creates_row(app_on) -> None:
    client = app_on.test_client()
    resp = client.post(
        "/api/contacts/upsert",
        json={"pin": "14210010010000", "email": "js@example.com"},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()
    assert data["contact"]["email"] == "js@example.com"


def test_upsert_contact_rejects_bad_email(app_on) -> None:
    client = app_on.test_client()
    resp = client.post(
        "/api/contacts/upsert",
        json={"pin": "14210010010000", "email": "not-an-email"},
    )
    assert resp.status_code == 400


def test_upsert_contact_rejects_bad_pin(app_on) -> None:
    client = app_on.test_client()
    resp = client.post(
        "/api/contacts/upsert",
        json={"pin": "short", "email": "a@b.com"},
    )
    assert resp.status_code == 400


def test_upsert_contact_rejects_null_pin(app_on) -> None:
    """JSON null for pin should land as 400, not 500."""
    client = app_on.test_client()
    resp = client.post(
        "/api/contacts/upsert",
        json={"pin": None, "email": "a@b.com"},
    )
    assert resp.status_code == 400


def test_send_outreach_rejects_null_pin_and_to(app_on) -> None:
    """JSON null on pin or `to` should land as 400, not 500."""
    client = app_on.test_client()
    resp = client.post("/api/outreach/send", json={
        "pin": None, "to": "x@y.com", "subject": "s", "body": "b",
    })
    assert resp.status_code == 400
    resp = client.post("/api/outreach/send", json={
        "pin": "14210010010000", "to": None, "subject": "s", "body": "b",
    })
    assert resp.status_code == 400


# ---------- GET /api/outreach/templates ----------

def test_get_templates_returns_list(app_on) -> None:
    client = app_on.test_client()
    resp = client.get("/api/outreach/templates")
    assert resp.status_code == 200
    data = resp.get_json()
    assert any(t["name"] == "t1" for t in data["templates"])


def test_get_templates_includes_rendered_preview_for_pin(app_on) -> None:
    """When ?pin= is supplied, templates come pre-rendered with that parcel's
    merge variables — that's what feeds the compose modal."""
    client = app_on.test_client()
    resp = client.get("/api/outreach/templates?pin=14210010010000")
    assert resp.status_code == 200
    data = resp.get_json()
    t = next(t for t in data["templates"] if t["name"] == "t1")
    # owner_first_name "John" comes from owner_name "JOHN SMITH"
    assert t["rendered_subject"] == "Hi John"
    assert t["rendered_body"] == "About 123 W Main St"


def test_save_template_creates_new(app_on, templates_path: Path) -> None:
    client = app_on.test_client()
    resp = client.post(
        "/api/outreach/templates/save",
        json={
            "name": "follow-up",
            "label": "Follow-up",
            "subject": "Re: {{address}}",
            "body": "Quick follow-up.",
        },
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    # Templates endpoint now lists both
    resp2 = client.get("/api/outreach/templates")
    names = [t["name"] for t in resp2.get_json()["templates"]]
    assert "t1" in names and "follow-up" in names


def test_save_template_overwrites_existing(app_on, templates_path: Path) -> None:
    client = app_on.test_client()
    client.post("/api/outreach/templates/save", json={
        "name": "t1", "subject": "Different subject",
        "body": "Different body",
    })
    resp = client.get("/api/outreach/templates")
    t1 = next(t for t in resp.get_json()["templates"] if t["name"] == "t1")
    assert t1["subject"] == "Different subject"


def test_save_template_rejects_empty_name(app_on) -> None:
    client = app_on.test_client()
    resp = client.post("/api/outreach/templates/save", json={
        "name": "", "subject": "s", "body": "b",
    })
    assert resp.status_code == 400


def test_save_template_rejects_dangerous_name(app_on) -> None:
    client = app_on.test_client()
    # Path traversal-ish or special chars should be refused.
    for bad in ["../escape", "name/with/slash", "name.with.dot", "ñame"]:
        resp = client.post("/api/outreach/templates/save", json={
            "name": bad, "subject": "s", "body": "b",
        })
        assert resp.status_code == 400, f"expected 400 for name={bad!r}"


def test_save_template_404_when_flag_off(app_off) -> None:
    client = app_off.test_client()
    resp = client.post("/api/outreach/templates/save", json={
        "name": "t1", "subject": "s", "body": "b",
    })
    assert resp.status_code == 404


# ---------- POST /api/outreach/send ----------

def test_send_outreach_calls_gmail_and_records_row(app_on) -> None:
    client = app_on.test_client()
    with patch("webapp.routes.gmail_client.send_email") as send_mock:
        send_mock.return_value = {"id": "msg-1", "threadId": "thr-1"}
        resp = client.post(
            "/api/outreach/send",
            json={
                "pin": "14210010010000",
                "to": "js@example.com",
                "subject": "Hi",
                "body": "Body",
            },
        )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()
    assert data["outreach_id"] >= 1
    assert data["gmail_message_id"] == "msg-1"
    # Send was called with sanitized subject and the right addresses
    assert send_mock.call_count == 1
    kwargs = send_mock.call_args.kwargs
    assert kwargs["sender"] == "me@example.com"
    assert kwargs["to"] == "js@example.com"
    assert kwargs["subject"] == "Hi"


def test_send_outreach_flips_stage_to_outreach(app_on, outreach_db_path: Path) -> None:
    client = app_on.test_client()
    with patch("webapp.routes.gmail_client.send_email") as send_mock:
        send_mock.return_value = {"id": "m", "threadId": "t"}
        client.post("/api/outreach/send", json={
            "pin": "14210010010000", "to": "x@y.com",
            "subject": "s", "body": "b",
        })
    conn = sqlite3.connect(outreach_db_path)
    stage = conn.execute(
        "SELECT stage FROM parcels WHERE pin = ?", ("14210010010000",)
    ).fetchone()[0]
    conn.close()
    assert stage == "outreach"


def test_send_outreach_sanitizes_subject(app_on) -> None:
    client = app_on.test_client()
    with patch("webapp.routes.gmail_client.send_email") as send_mock:
        send_mock.return_value = {"id": "m", "threadId": "t"}
        client.post("/api/outreach/send", json={
            "pin": "14210010010000", "to": "x@y.com",
            "subject": "Hi\r\nBcc: evil@x.com", "body": "b",
        })
    assert send_mock.call_args.kwargs["subject"] == "HiBcc: evil@x.com"


def test_send_outreach_surfaces_gmail_error(app_on) -> None:
    from pipeline.gmail_client import GmailNotConnectedError
    client = app_on.test_client()
    with patch("webapp.routes.gmail_client.send_email") as send_mock:
        send_mock.side_effect = GmailNotConnectedError("nope")
        resp = client.post("/api/outreach/send", json={
            "pin": "14210010010000", "to": "x@y.com",
            "subject": "s", "body": "b",
        })
    assert resp.status_code == 503
    assert "not connected" in resp.get_data(as_text=True).lower()


def test_send_outreach_surfaces_gmail_http_error(app_on) -> None:
    """Gmail API quota / 5xx / forbidden — surface as 503 with the API reason
    so the UI can show something actionable instead of a generic 500."""
    from googleapiclient.errors import HttpError
    client = app_on.test_client()
    # Build a minimal HttpError. The googleapiclient constructor expects a
    # response-like object with .status and a content bytestring.
    from unittest.mock import MagicMock
    fake_resp = MagicMock()
    fake_resp.status = 429
    fake_resp.reason = "Too Many Requests"
    err = HttpError(fake_resp, b'{"error":{"message":"quota exceeded"}}')

    with patch("webapp.routes.gmail_client.send_email") as send_mock:
        send_mock.side_effect = err
        resp = client.post("/api/outreach/send", json={
            "pin": "14210010010000", "to": "x@y.com",
            "subject": "s", "body": "b",
        })
    assert resp.status_code == 503
    assert "gmail api error" in resp.get_data(as_text=True).lower()


def test_send_outreach_rejects_missing_fields(app_on) -> None:
    client = app_on.test_client()
    resp = client.post("/api/outreach/send", json={
        "pin": "14210010010000", "to": "x@y.com", "subject": "s",  # body missing
    })
    assert resp.status_code == 400


# ---------- POST /api/outreach/<id>/mark-replied ----------

def test_mark_replied_updates_row(app_on, outreach_db_path: Path) -> None:
    client = app_on.test_client()
    with patch("webapp.routes.gmail_client.send_email") as send_mock:
        send_mock.return_value = {"id": "m", "threadId": "t"}
        resp = client.post("/api/outreach/send", json={
            "pin": "14210010010000", "to": "x@y.com",
            "subject": "s", "body": "b",
        })
    oid = resp.get_json()["outreach_id"]

    resp = client.post(f"/api/outreach/{oid}/mark-replied", json={
        "response_type": "responded"
    })
    assert resp.status_code == 200
    conn = sqlite3.connect(outreach_db_path)
    row = conn.execute(
        "SELECT response_date, response_type FROM outreach WHERE outreach_id = ?",
        (oid,),
    ).fetchone()
    conn.close()
    assert row[0] is not None
    assert row[1] == "responded"


# ---------- POST /api/parcels/<pin>/stage ----------

def test_set_stage_updates_parcel(app_on, outreach_db_path: Path) -> None:
    client = app_on.test_client()
    resp = client.post("/api/parcels/14210010010000/stage",
                       json={"stage": "dead"})
    assert resp.status_code == 200
    conn = sqlite3.connect(outreach_db_path)
    stage = conn.execute(
        "SELECT stage FROM parcels WHERE pin = ?", ("14210010010000",)
    ).fetchone()[0]
    conn.close()
    assert stage == "dead"


def test_set_stage_rejects_bad_value(app_on) -> None:
    client = app_on.test_client()
    resp = client.post("/api/parcels/14210010010000/stage",
                       json={"stage": "bogus"})
    assert resp.status_code == 400


# ---------- OAuth routes ----------

def test_oauth_start_redirects_to_google(app_on, tmp_path: Path) -> None:
    """OAuth start kicks the user over to Google's consent page."""
    # The client_secrets file needs to exist for the Flow library to read it,
    # but we mock Flow itself.
    (tmp_path / "client.json").write_text(json.dumps({
        "web": {
            "client_id": "cid", "client_secret": "s",
            "redirect_uris": ["http://localhost:5051/api/oauth/callback"],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }))
    with patch("pipeline.gmail_client.build_authorization_url") as ba:
        ba.return_value = ("https://accounts.google.com/auth?x=1", "state-abc")
        client = app_on.test_client()
        resp = client.get("/api/oauth/start")
    assert resp.status_code == 302
    assert resp.location == "https://accounts.google.com/auth?x=1"


def test_oauth_start_404s_when_client_secrets_missing(app_on) -> None:
    """If the user hasn't placed the Google client JSON, we tell them so."""
    client = app_on.test_client()
    resp = client.get("/api/oauth/start")
    assert resp.status_code == 503
    assert "client" in resp.get_data(as_text=True).lower()


# ---------- T8: cadence-aware behaviors on existing routes ----------

def test_send_outreach_accepts_touch_number(app_on, outreach_db_path):
    """Send with touch_number=2 records the outreach row with that touch."""
    import sqlite3
    conn = sqlite3.connect(outreach_db_path)
    conn.execute(
        "INSERT INTO outreach (pin, touch_number, channel, sent_date) "
        "VALUES (?, ?, ?, ?)",
        ("14210010010000", 1, "email", "2026-05-08T09:00:00Z"),
    )
    conn.commit()
    conn.close()
    with patch("webapp.routes.gmail_client.send_email") as send_mock:
        send_mock.return_value = {"id": "m2", "threadId": "t2"}
        resp = app_on.test_client().post("/api/outreach/send", json={
            "pin": "14210010010000", "to": "x@y.com",
            "subject": "Touch 2", "body": "Day 3 follow-up",
            "touch_number": 2,
        })
    assert resp.status_code == 200, resp.get_data(as_text=True)
    conn = sqlite3.connect(outreach_db_path)
    row = conn.execute(
        "SELECT touch_number FROM outreach WHERE outreach_id = ?",
        (resp.get_json()["outreach_id"],),
    ).fetchone()
    conn.close()
    assert row[0] == 2


def test_send_outreach_rejects_out_of_order_touch(app_on):
    """Posting touch 5 on a parcel with no prior outreach history → 400
    (expected touch is 1; 5 is out of order). validate_next_due_touch
    catches this before any Gmail send happens — no mock needed."""
    resp = app_on.test_client().post("/api/outreach/send", json={
        "pin": "14210010010000", "to": "x@y.com",
        "subject": "s", "body": "b", "touch_number": 5,
    })
    assert resp.status_code == 400


def test_send_persists_gmail_message_id_to_dedicated_column(app_on, outreach_db_path):
    """The send route writes the Gmail message id to its dedicated column,
    NOT polluting the notes field. Replaces the prior 'gmail_message_id=X'
    string-in-notes hack."""
    import sqlite3
    with patch("webapp.routes.gmail_client.send_email") as send_mock:
        send_mock.return_value = {"id": "msg-abc-123", "threadId": "thr-1"}
        resp = app_on.test_client().post("/api/outreach/send", json={
            "pin": "14210010010000", "to": "x@y.com",
            "subject": "s", "body": "b",
        })
    assert resp.status_code == 200
    oid = resp.get_json()["outreach_id"]
    conn = sqlite3.connect(outreach_db_path)
    row = conn.execute(
        "SELECT gmail_message_id, notes FROM outreach WHERE outreach_id = ?",
        (oid,),
    ).fetchone()
    conn.close()
    assert row[0] == "msg-abc-123"
    # notes must NOT be polluted with the old "gmail_message_id=X" format
    assert row[1] is None or "gmail_message_id=" not in row[1]


def test_send_outreach_defaults_touch_number_to_1(app_on, outreach_db_path):
    """Backward-compat: omitting touch_number sends touch 1."""
    with patch("webapp.routes.gmail_client.send_email") as send_mock:
        send_mock.return_value = {"id": "m", "threadId": "t"}
        resp = app_on.test_client().post("/api/outreach/send", json={
            "pin": "14210010010000", "to": "x@y.com",
            "subject": "s", "body": "b",
        })
    assert resp.status_code == 200
    import sqlite3
    conn = sqlite3.connect(outreach_db_path)
    row = conn.execute(
        "SELECT touch_number FROM outreach WHERE outreach_id = ?",
        (resp.get_json()["outreach_id"],),
    ).fetchone()
    conn.close()
    assert row[0] == 1


def test_get_parcel_outreach_includes_sequence_block(app_on, outreach_db_path):
    """The detail endpoint returns a `sequence` block describing the
    parcel's cadence state."""
    import sqlite3
    conn = sqlite3.connect(outreach_db_path)
    conn.execute(
        "UPDATE parcels SET stage = 'outreach', mail_address = ? WHERE pin = ?",
        ("500 N Main", "14210010010000"),
    )
    conn.execute(
        "INSERT INTO outreach (pin, touch_number, channel, sent_date) "
        "VALUES (?, ?, ?, ?)",
        ("14210010010000", 1, "email", "2026-05-08T09:00:00Z"),
    )
    conn.execute(
        "INSERT INTO contacts (pin, email, source) VALUES (?, ?, ?)",
        ("14210010010000", "js@example.com", "manual"),
    )
    conn.commit()
    conn.close()
    resp = app_on.test_client().get("/api/parcels/14210010010000/outreach")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "sequence" in data
    seq = data["sequence"]
    assert seq["anchor_date"] == "2026-05-08"
    assert seq["current_touch"] == 1
    assert seq["next_due"] is not None
    assert seq["next_due"]["touch"] == 2
    assert seq["is_end_of_sequence"] is False
    assert seq["is_paused"] is False


def test_get_parcel_outreach_sequence_paused(app_on, outreach_db_path):
    """When a parcel is paused, the sequence block reports it AND
    suppresses next_due (the cadence engine never gets called)."""
    import sqlite3
    conn = sqlite3.connect(outreach_db_path)
    conn.execute(
        "UPDATE parcels SET outreach_paused = 1 WHERE pin = ?",
        ("14210010010000",),
    )
    conn.commit()
    conn.close()
    resp = app_on.test_client().get("/api/parcels/14210010010000/outreach")
    seq = resp.get_json()["sequence"]
    assert seq["is_paused"] is True
    # With no outreach rows AND paused, all derived fields stay defaults
    assert seq["next_due"] is None
    assert seq["anchor_date"] is None
    assert seq["current_touch"] == 0
