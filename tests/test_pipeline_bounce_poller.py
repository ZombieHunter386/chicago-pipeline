"""Tests for pipeline/bounce_poller.py — parser + poll_once integration.

The parser tests exercise both extraction paths:
  - Final-Recipient: rfc822; ... (the structured RFC 3464 header)
  - <addr> angle-bracket fallback (heuristic for bouncebacks that omit
    Final-Recipient but quote the failed address in the human body)

The poll_once integration test stubs out gmail_client.fetch_mailer_daemon_messages
so no real Gmail API calls are made — we never want to touch the network
or send any email during tests.
"""
from __future__ import annotations
import sqlite3
from pathlib import Path
import pytest
from pipeline.bounce_poller import extract_failed_recipients


FIXTURES = Path(__file__).parent / "fixtures" / "bounces"


def test_extract_from_gmail_hard_bounce():
    body = (FIXTURES / "gmail_hard_bounce.eml").read_text()
    addresses = extract_failed_recipients(body)
    assert len(addresses) >= 1
    assert all("@" in a for a in addresses)
    assert "nonexistent-recipient-test@gmail.com" in addresses


def test_extract_from_gmail_recipient_unknown():
    body = (FIXTURES / "gmail_recipient_unknown.eml").read_text()
    addresses = extract_failed_recipients(body)
    assert len(addresses) >= 1
    assert "test@nonexistent-domain-fake-12345.com" in addresses


def test_extract_returns_empty_for_non_bounce():
    addresses = extract_failed_recipients("Hello, just a normal email.\n")
    assert addresses == []


def test_extract_dedup():
    body = """
    Final-Recipient: rfc822; bouncy@example.com
    Final-Recipient: rfc822; bouncy@example.com
    """
    assert extract_failed_recipients(body) == ["bouncy@example.com"]


def test_extract_angle_addr_fallback_when_no_final_recipient():
    # No Final-Recipient header — parser must fall back to <addr>
    # heuristic from the human-readable body section. The googlemail.com
    # / google.com domains are filtered out to avoid catching the
    # mailer-daemon's own From: header.
    body = """
    From: Mail Delivery Subsystem <mailer-daemon@googlemail.com>
    Subject: Delivery Status Notification (Failure)

    The following message to <missing-user@somedomain.example> was
    undeliverable.
    """
    addrs = extract_failed_recipients(body)
    assert addrs == ["missing-user@somedomain.example"]


def test_poll_once_flips_matching_contacts(tmp_path):
    from pipeline.db import init_db
    from pipeline.bounce_poller import poll_once
    db = tmp_path / "t.db"
    init_db(db)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO parcels(pin, owner_name, mail_address) "
            "VALUES ('14000000000001', 'X', 'Y')"
        )
        conn.execute(
            "INSERT INTO contacts(pin, email, source) "
            "VALUES ('14000000000001', 'bouncy@example.com', 'enrichment')"
        )
        conn.execute(
            "INSERT INTO contacts(pin, email, source) "
            "VALUES ('14000000000001', 'good@example.com', 'enrichment')"
        )
        conn.commit()
    fake_body = (FIXTURES / "gmail_hard_bounce.eml").read_text()
    # Substitute bouncy@example.com into the fixture (the synthetic fixture
    # has 'nonexistent-recipient-test@gmail.com' as the failed recipient;
    # we want this test to verify our specific contact gets marked).
    if "bouncy@example.com" not in fake_body:
        fake_body += "\nFinal-Recipient: rfc822; bouncy@example.com\n"
    result = poll_once(
        db_path=db, gmail_token_path=tmp_path / "token.json",
        fetch_messages_fn=lambda **kw: [("msg-1", fake_body)],
    )
    assert result["addresses_flipped"] >= 1
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        rows = {r["email"]: r["dead"] for r in conn.execute(
            "SELECT email, dead FROM contacts WHERE pin='14000000000001'"
        )}
    assert rows["bouncy@example.com"] == 1
    assert rows["good@example.com"] == 0
