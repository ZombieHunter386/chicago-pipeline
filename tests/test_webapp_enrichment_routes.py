from __future__ import annotations
import json
import sqlite3
from pathlib import Path
import pytest
from pipeline.db import init_db
from pipeline.enrichment import (
    EnrichmentContact, EnrichmentResult, BudgetCap,
)
from webapp.app import create_app


@pytest.fixture
def app(tmp_path):
    db = tmp_path / "t.db"
    init_db(db)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO parcels(pin, owner_name, mail_address, is_llc) "
            "VALUES ('14000000000001', 'John Smith', '111 Main', 0)"
        )
        conn.commit()

    class StubSkip:
        name = "stub"
        cost_per_lookup_usd = 0.10
        def lookup(self, **_kwargs):  # accepts default_city/state/zip etc
            return EnrichmentResult(
                contacts=[EnrichmentContact(
                    value="john@x.com", kind="email",
                    confidence_pct=None,
                    source_label="stub:email:rank-1:via=John Smith")],
                raw_response_json="{}", cost_usd=0.10,
                provider="stub", status="success", error_message=None,
            )

    app = create_app(db_path=db, feature_outreach=True)
    app.config["ENRICHMENT_SKIP_PROVIDER"] = StubSkip()
    app.config["ENRICHMENT_BUDGET"] = BudgetCap(
        soft_daily_usd=100.0, hard_per_run_usd=100.0,
    )
    return app


def test_post_enrichment_lookup_creates_contact(app):
    client = app.test_client()
    r = client.post("/api/enrichment/lookup/14000000000001")
    assert r.status_code == 200
    data = r.get_json()
    assert data["status"] == "success"
    assert len(data["contacts"]) >= 1


def test_post_enrichment_lookup_404_unknown_pin(app):
    client = app.test_client()
    r = client.post("/api/enrichment/lookup/99999999999999")
    assert r.status_code == 404


def test_post_enrichment_lookup_409_already_has_contacts(app):
    client = app.test_client()
    client.post("/api/enrichment/lookup/14000000000001")
    r = client.post("/api/enrichment/lookup/14000000000001")
    assert r.status_code == 409


def test_post_enrichment_lookup_allows_retrace_when_all_contacts_dead(app):
    """The 409 'already has contacts' gate must only count alive contacts.
    If every existing contact is dead or wrong_person, the operator has
    no usable contact and must be allowed to spend another $0.10 to look
    for new ones."""
    client = app.test_client()
    # Seed first trace, then mark the resulting contact dead
    client.post("/api/enrichment/lookup/14000000000001")
    with app.app_context():
        from webapp.routes import _conn
        with _conn() as conn:
            cid = conn.execute(
                "SELECT contact_id FROM contacts WHERE pin='14000000000001'"
            ).fetchone()[0]
    client.post(f"/api/contacts/{cid}/dead")
    # Re-trace should now succeed (not 409)
    r = client.post("/api/enrichment/lookup/14000000000001")
    assert r.status_code == 200, \
        f"expected re-trace allowed once contacts are dead; got {r.status_code} {r.get_data(as_text=True)}"


def test_post_enrichment_lookup_allows_retrace_when_all_contacts_wrong_person(app):
    """Symmetric to the dead case — wrong_person contacts shouldn't gate re-trace either."""
    client = app.test_client()
    client.post("/api/enrichment/lookup/14000000000001")
    with app.app_context():
        from webapp.routes import _conn
        with _conn() as conn:
            cid = conn.execute(
                "SELECT contact_id FROM contacts WHERE pin='14000000000001'"
            ).fetchone()[0]
    client.post(f"/api/contacts/{cid}/wrong-person")
    r = client.post("/api/enrichment/lookup/14000000000001")
    assert r.status_code == 200


def test_post_enrichment_lookup_502_when_provider_errors(app):
    """When the provider returns status='error' (e.g. Tracerfy 400 on a
    malformed payload), the endpoint must surface that as 502 so the UI can
    show the operator what went wrong. Previously this returned 200 with an
    empty contacts list, indistinguishable from a legitimate no-hit miss."""
    class ErrorProvider:
        name = "stub-error"
        cost_per_lookup_usd = 0.10
        def lookup(self, **_kwargs):
            return EnrichmentResult(
                contacts=[], raw_response_json='{"city":["required"]}',
                cost_usd=0.0, provider="stub-error", status="error",
                error_message='HTTP 400: {"city":["required"]}',
            )
    app.config["ENRICHMENT_SKIP_PROVIDER"] = ErrorProvider()

    client = app.test_client()
    r = client.post("/api/enrichment/lookup/14000000000001")
    assert r.status_code == 502
    # Flask's abort(502, msg) puts the message in the response body
    assert "HTTP 400" in r.get_data(as_text=True)


def test_post_enrichment_bulk_kicks_off_job(app):
    client = app.test_client()
    r = client.post("/api/enrichment/bulk", json={"pins": ["14000000000001"]})
    assert r.status_code == 202
    job_id = r.get_json()["job_id"]
    import time
    for _ in range(50):
        time.sleep(0.05)
        s = client.get(f"/api/enrichment/job/{job_id}").get_json()
        if s["status"] in ("complete", "failed", "paused"):
            break
    assert s["status"] == "complete"


def test_post_contact_mark_dead(app):
    client = app.test_client()
    client.post("/api/enrichment/lookup/14000000000001")
    with app.app_context():
        from webapp.routes import _conn
        with _conn() as conn:
            row = conn.execute(
                "SELECT contact_id FROM contacts WHERE pin='14000000000001' LIMIT 1"
            ).fetchone()
    cid = row[0]
    r = client.post(f"/api/contacts/{cid}/dead")
    assert r.status_code == 200
    with app.app_context():
        with _conn() as conn:
            dead = conn.execute(
                "SELECT dead FROM contacts WHERE contact_id=?", (cid,)
            ).fetchone()[0]
    assert dead == 1


def test_post_contact_mark_wrong_person(app):
    client = app.test_client()
    client.post("/api/enrichment/lookup/14000000000001")
    with app.app_context():
        from webapp.routes import _conn
        with _conn() as conn:
            cid = conn.execute(
                "SELECT contact_id FROM contacts WHERE pin='14000000000001' LIMIT 1"
            ).fetchone()[0]
    r = client.post(f"/api/contacts/{cid}/wrong-person")
    assert r.status_code == 200


def test_outreach_send_503_when_refresh_token_revoked(app, monkeypatch):
    """When the Gmail refresh token's been revoked (Google's 7-day idle
    expiry, or user revoked at myaccount.google.com), the send endpoint
    must return 503 'Gmail not connected' so the UI can prompt re-consent
    — not 500 internal_error which leaks raw OAuth gunk to the operator."""
    import google.auth.exceptions
    from pipeline import gmail_client

    client = app.test_client()
    client.post("/api/enrichment/lookup/14000000000001")  # seed alive contact
    app.config["GMAIL_SENDER_ADDRESS"] = "me@example.com"

    def revoked(*args, **kwargs):
        raise google.auth.exceptions.RefreshError(
            "invalid_grant: Token has been expired or revoked.",
            {"error": "invalid_grant"},
        )
    monkeypatch.setattr(gmail_client, "send_email", revoked)

    r = client.post("/api/outreach/send", json={
        "pin": "14000000000001",
        "to_list": ["john@x.com"],
        "subject": "hi", "body": "hello",
        "touch_number": 1,
    })
    assert r.status_code == 503
    body = r.get_data(as_text=True)
    assert "Gmail" in body or "oauth" in body.lower()


def test_send_with_to_list_uses_bcc(app, monkeypatch):
    """POST /api/outreach/send accepts to_list and the request body sends
    via BCC (preserving the visible To: header as the sender)."""
    client = app.test_client()
    client.post("/api/enrichment/lookup/14000000000001")
    captured = {}
    from pipeline import gmail_client
    def fake_send(**kw):
        captured.update(kw)
        return {"id": "x", "threadId": "y"}
    monkeypatch.setattr(gmail_client, "send_email", fake_send)
    app.config["GMAIL_SENDER_ADDRESS"] = "me@example.com"

    r = client.post("/api/outreach/send", json={
        "pin": "14000000000001",
        "to_list": ["john@x.com"],
        "subject": "hi", "body": "hello",
        "touch_number": 1,
    })
    assert r.status_code == 200
    assert captured["bcc"] == ["john@x.com"]
    assert captured["to"] == "me@example.com"
