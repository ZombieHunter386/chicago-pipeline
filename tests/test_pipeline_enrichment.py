from __future__ import annotations
import sqlite3
import pytest
from pipeline.db import init_db
from pipeline.enrichment import (
    alive_emails_for_parcel,
    alive_phones_for_parcel,
    EnrichmentContact,
    EnrichmentResult,
    _enrich_one_pin,
)


def test_alive_emails_filters_dead_and_wrong():
    contacts = [
        {"email": "a@x.com", "dead": 0, "wrong_person": 0},
        {"email": "b@x.com", "dead": 1, "wrong_person": 0},
        {"email": "c@x.com", "dead": 0, "wrong_person": 1},
        {"email": None,        "dead": 0, "wrong_person": 0},
        {"email": "d@x.com", "dead": 0, "wrong_person": 0},
    ]
    assert alive_emails_for_parcel(contacts) == ["a@x.com", "d@x.com"]


def test_alive_phones_filters_dead_and_wrong():
    contacts = [
        {"phone": "312-555-0001", "dead": 0, "wrong_person": 0},
        {"phone": "312-555-0002", "dead": 1, "wrong_person": 0},
        {"phone": None,            "dead": 0, "wrong_person": 0},
        {"phone": "312-555-0003", "dead": 0, "wrong_person": 0},
    ]
    assert alive_phones_for_parcel(contacts) == ["312-555-0001", "312-555-0003"]


def test_enrichment_contact_frozen():
    c = EnrichmentContact(value="a@x.com", kind="email",
                          confidence_pct=85, source_label="tracerfy:email:rank-1")
    with pytest.raises(Exception):
        c.value = "b@x.com"  # dataclass(frozen=True) raises FrozenInstanceError


def test_budget_cap_soft_threshold(tmp_path):
    import sqlite3
    from pipeline.enrichment import BudgetCap
    from pipeline.db import init_db
    db = tmp_path / "t.db"
    init_db(db)
    cap = BudgetCap(soft_daily_usd=5.00, hard_per_run_usd=2.00)
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        # Below soft cap
        assert cap.would_exceed_soft(conn, additional_cost=1.00) is False
        # Spike daily total close to the cap
        for _ in range(40):
            conn.execute(
                "INSERT INTO enrichment_results(pin, provider, lookup_type, "
                "query_name, raw_response_json, cost_usd, status) "
                "VALUES ('14000000000000', 'test', 'skip_trace', 'X', '{}', 0.10, 'success')"
            )
        conn.commit()  # $4 spent
        assert cap.would_exceed_soft(conn, additional_cost=0.50) is False
        assert cap.would_exceed_soft(conn, additional_cost=2.00) is True


class _RecordingProvider:
    """Stub provider that captures every kwarg passed to lookup() so tests
    can assert on the structured fields _enrich_one_pin chose to send."""
    name = "stub"
    cost_per_lookup_usd = 0.10

    def __init__(self):
        self.calls: list[dict] = []

    def lookup(self, **kwargs):
        self.calls.append(kwargs)
        return EnrichmentResult(
            contacts=[], raw_response_json="{}", cost_usd=0.0,
            provider=self.name, status="success", error_message=None,
        )


def _seed_parcel(db, *, pin="14192080160000", is_llc=0):
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO parcels(pin, owner_name, mail_address, zip_code, is_llc) "
            "VALUES (?, 'Resurrection Cov Ch', '3901 N MARSHFIELD AVE', '60613', ?)",
            (pin, is_llc),
        )
        conn.commit()


def _run_enrich(db, pin="14192080160000", provider=None):
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        return _enrich_one_pin(conn, job_id=None, pin=pin, provider=provider)


@pytest.mark.parametrize("is_llc", [0, 1])
def test_enrich_one_pin_always_uses_advanced_regardless_of_is_llc(tmp_path, is_llc):
    """Tracerfy advanced (find_owner=true) is strictly equal-or-better than
    normal mode in every scenario we care about — more contacts per $0.10,
    catches stale assessor owner_name, simpler code path. is_llc no longer
    routes; every pin goes through advanced.
    """
    db = tmp_path / "t.db"
    init_db(db)
    _seed_parcel(db, is_llc=is_llc)
    provider = _RecordingProvider()
    _run_enrich(db, provider=provider)

    assert len(provider.calls) == 1, "advanced-only — exactly one provider call per pin"
    call = provider.calls[0]
    # No name fields sent — advanced mode is purely address-based.
    assert "owner_first_name" not in call or not call.get("owner_first_name")
    assert "owner_last_name" not in call or not call.get("owner_last_name")
    # Always persisted with the advanced lookup_type so the audit log is
    # consistent.
    with sqlite3.connect(db) as conn:
        lookup_types = [r[0] for r in conn.execute(
            "SELECT lookup_type FROM enrichment_results WHERE pin='14192080160000'"
        )]
    assert lookup_types == ["skip_trace_advanced"]


def test_enrich_one_pin_persists_no_match_with_zero_contacts(tmp_path):
    """An advanced no_match writes a single result row (status='no_match',
    zero contacts) and the function returns that result so the endpoint
    can surface 'No records found'."""
    db = tmp_path / "t.db"
    init_db(db)
    _seed_parcel(db)

    class _MissProvider:
        name = "stub"
        cost_per_lookup_usd = 0.10
        calls = 0
        def lookup(self, **_kw):
            type(self).calls += 1
            return EnrichmentResult(
                contacts=[], raw_response_json="{}", cost_usd=0.0,
                provider="stub", status="no_match", error_message=None,
            )

    provider = _MissProvider()
    result = _run_enrich(db, provider=provider)

    assert _MissProvider.calls == 1, "no retry, no fallback — single call"
    assert result.status == "no_match"
    assert result.contacts == []
    with sqlite3.connect(db) as conn:
        n_contacts = conn.execute(
            "SELECT COUNT(*) FROM contacts WHERE pin='14192080160000'"
        ).fetchone()[0]
    assert n_contacts == 0


def test_enrich_one_pin_defaults_city_state_zip_for_chicago(tmp_path):
    """Cook County assessor mail_address is street-only ('3550 N LAKE SHORE DR')
    — no city/state/zip. _enrich_one_pin must hand the provider Chicago
    defaults + the parcel's zip_code so Tracerfy's required-field check passes.
    """
    db = tmp_path / "t.db"
    init_db(db)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO parcels(pin, owner_name, mail_address, zip_code, is_llc) "
            "VALUES ('14211110071178', 'John Morris', "
            "'3550 N LAKE SHORE DR', '60657', 0)"
        )
        conn.commit()

    provider = _RecordingProvider()
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        _enrich_one_pin(conn, job_id=None, pin="14211110071178",
                        provider=provider)

    assert len(provider.calls) == 1
    call = provider.calls[0]
    assert call.get("default_city") == "Chicago"
    assert call.get("default_state") == "IL"
    assert call.get("default_zip") == "60657"


def test_budget_cap_hard_per_run(tmp_path):
    import sqlite3
    from pipeline.enrichment import BudgetCap, BudgetExceeded
    from pipeline.db import init_db
    db = tmp_path / "t.db"
    init_db(db)
    cap = BudgetCap(soft_daily_usd=999.0, hard_per_run_usd=2.00)
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute(
            "INSERT INTO enrichment_jobs(pin_list_json, status, total_cost_usd) "
            "VALUES ('[]', 'running', 1.95)"
        )
        job_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
        # Just under
        cap.check_or_raise(conn, job_id=job_id, additional_cost=0.04)
        # Over
        with pytest.raises(BudgetExceeded):
            cap.check_or_raise(conn, job_id=job_id, additional_cost=0.10)
