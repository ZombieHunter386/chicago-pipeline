from __future__ import annotations
import json
from pathlib import Path
import pytest
from pipeline.enrichment_providers.tracerfy import (
    TracerfyProvider, parse_mail_address,
)


FIXTURE_NORMAL = Path(__file__).parent / "fixtures" / "tracerfy_normal.json"
FIXTURE_ADVANCED = Path(__file__).parent / "fixtures" / "tracerfy_advanced.json"


# ---------- Parser tests (no network) ----------

def test_parse_normal_response_returns_one_person_with_contacts():
    p = TracerfyProvider(api_key="fake-key")
    raw = json.loads(FIXTURE_NORMAL.read_text())
    result = p._parse_response(raw)
    assert result.provider == "tracerfy"
    assert result.status == "success"
    assert result.cost_usd == 0.10
    emails = [c for c in result.contacts if c.kind == "email"]
    phones = [c for c in result.contacts if c.kind == "phone"]
    assert len(emails) >= 1
    assert len(phones) >= 1
    assert all("via=" in c.source_label for c in result.contacts)


def test_parse_advanced_response_returns_multiple_persons_grouped():
    p = TracerfyProvider(api_key="fake-key")
    raw = json.loads(FIXTURE_ADVANCED.read_text())
    result = p._parse_response(raw)
    assert result.status == "success"
    persons_seen = set()
    for c in result.contacts:
        for part in c.source_label.split(":"):
            if part.startswith("via="):
                persons_seen.add(part[len("via="):])
    assert len(persons_seen) == 3


def test_parse_no_hit_returns_no_match_zero_cost():
    p = TracerfyProvider(api_key="fake-key")
    raw = {
        "address": "X", "city": "Y", "state": "IL",
        "find_owner": False, "hit": False,
        "persons_count": 0, "credits_deducted": 0, "persons": [],
    }
    result = p._parse_response(raw)
    assert result.status == "no_match"
    assert result.contacts == []
    assert result.cost_usd == 0.0


def test_parse_skips_deceased_persons():
    p = TracerfyProvider(api_key="fake-key")
    raw = {
        "address": "X", "city": "Y", "state": "IL",
        "find_owner": False, "hit": True,
        "persons_count": 1, "credits_deducted": 5,
        "persons": [{
            "first_name": "Ghost", "last_name": "Smith",
            "full_name": "Ghost Smith",
            "deceased": True, "property_owner": True, "litigator": False,
            "phones": [{"number": "3125550001", "type": "Mobile",
                        "dnc": False, "carrier": "", "rank": 1}],
            "emails": [{"email": "ghost@x.com", "rank": 1}],
        }],
    }
    result = p._parse_response(raw)
    assert result.status == "no_match"
    assert result.contacts == []


# ---------- Request-shape tests (mocked HTTP) ----------

def test_lookup_normal_mode_sends_first_last_names(monkeypatch):
    import requests
    captured = {}
    class FakeResp:
        status_code = 200
        text = FIXTURE_NORMAL.read_text()
        def json(self_inner):
            return json.loads(FIXTURE_NORMAL.read_text())
    def fake_post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return FakeResp()
    monkeypatch.setattr(requests, "post", fake_post)
    p = TracerfyProvider(api_key="fake-key")
    result = p.lookup(
        mail_address="123 Main St, Chicago, IL 60601",
        owner_first_name="John", owner_last_name="Smith",
    )
    assert captured["url"] == "https://tracerfy.com/v1/api/trace/lookup/"
    assert captured["headers"]["Authorization"] == "Bearer fake-key"
    body = captured["json"]
    assert body["find_owner"] is False
    assert body["first_name"] == "John"
    assert body["last_name"] == "Smith"
    assert body["state"] == "IL"
    assert body["city"] == "Chicago"
    assert body["address"].startswith("123 Main St")
    assert result.cost_usd == 0.10


def test_lookup_advanced_mode_when_names_missing(monkeypatch):
    """When first or last name is empty/None, falls through to advanced mode."""
    import requests
    captured = {}
    class FakeResp:
        status_code = 200
        text = FIXTURE_ADVANCED.read_text()
        def json(self_inner):
            return json.loads(FIXTURE_ADVANCED.read_text())
    def fake_post(url, json=None, headers=None, timeout=None):
        captured["json"] = json
        return FakeResp()
    monkeypatch.setattr(requests, "post", fake_post)
    p = TracerfyProvider(api_key="fake-key")
    p.lookup(mail_address="123 Main St, Chicago, IL 60601")
    assert captured["json"]["find_owner"] is True
    assert "first_name" not in captured["json"]
    assert "last_name" not in captured["json"]


@pytest.mark.parametrize("first,last", [
    ("", "Smith"),
    ("John", ""),
    (None, "Smith"),
    ("John", None),
    ("", ""),
])
def test_lookup_advanced_mode_when_either_name_blank(monkeypatch, first, last):
    import requests
    captured = {}
    class FakeResp:
        status_code = 200
        text = '{}'
        def json(self_inner):
            return {"hit": False, "persons_count": 0,
                    "credits_deducted": 0, "persons": []}
    def fake_post(url, json=None, headers=None, timeout=None):
        captured["json"] = json
        return FakeResp()
    monkeypatch.setattr(requests, "post", fake_post)
    p = TracerfyProvider(api_key="fake-key")
    p.lookup(mail_address="123 Main St, Chicago, IL 60601",
             owner_first_name=first, owner_last_name=last)
    assert captured["json"]["find_owner"] is True


def test_lookup_handles_429_rate_limit(monkeypatch):
    """Tracerfy rate-limits at 500 RPM; surface a clean error, don't crash."""
    import requests
    class FakeResp:
        status_code = 429
        text = '{"error":"Rate limit exceeded"}'
        def json(self_inner):
            return {"error": "Rate limit exceeded"}
    monkeypatch.setattr(requests, "post", lambda *a, **kw: FakeResp())
    p = TracerfyProvider(api_key="fake-key")
    result = p.lookup(mail_address="123 Main St",
                      owner_first_name="X", owner_last_name="Y")
    assert result.status == "error"
    assert "429" in (result.error_message or "")


def test_lookup_handles_500_error(monkeypatch):
    import requests
    class FakeResp:
        status_code = 500
        text = "server error"
        def json(self_inner):
            raise ValueError("not json")
    monkeypatch.setattr(requests, "post", lambda *a, **kw: FakeResp())
    p = TracerfyProvider(api_key="fake-key")
    result = p.lookup(mail_address="123 Main St",
                      owner_first_name="X", owner_last_name="Y")
    assert result.status == "error"
    assert result.contacts == []


# ---------- Address parser tests ----------

@pytest.mark.parametrize("raw_addr,expected", [
    ("123 Main St, Chicago, IL 60601",
     {"address": "123 Main St", "city": "Chicago", "state": "IL", "zip": "60601"}),
    ("123 Main St Chicago IL 60601",
     {"address": "123 Main St", "city": "Chicago", "state": "IL", "zip": "60601"}),
    ("123 Main St", {"address": "123 Main St", "city": "", "state": "", "zip": ""}),
    ("", {"address": "", "city": "", "state": "", "zip": ""}),
    ("123 Main St Apt 4, Chicago, IL 60601",
     {"address": "123 Main St Apt 4", "city": "Chicago", "state": "IL", "zip": "60601"}),
    # Real Cook County assessor mail_address strings — street-only, no
    # city/state/zip. The 2-letter suffixes (DR, RD, ST) must NOT be
    # mistaken for state codes; only the whitelist of real US state codes
    # counts as a state.
    ("3550 N LAKE SHORE DR",
     {"address": "3550 N LAKE SHORE DR", "city": "", "state": "", "zip": ""}),
    ("655 W IRVING PARK RD",
     {"address": "655 W IRVING PARK RD", "city": "", "state": "", "zip": ""}),
    ("1806 N HALSTED ST",
     {"address": "1806 N HALSTED ST", "city": "", "state": "", "zip": ""}),
])
def test_parse_mail_address(raw_addr, expected):
    assert parse_mail_address(raw_addr) == expected
