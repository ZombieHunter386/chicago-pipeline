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
