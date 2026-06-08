from __future__ import annotations
import sqlite3
import pytest
from pipeline.db import init_db
from pipeline.enrichment import (
    split_owner_name,
    alive_emails_for_parcel,
    alive_phones_for_parcel,
    EnrichmentContact,
    EnrichmentResult,
    _enrich_one_pin,
)


@pytest.mark.parametrize("raw,expected", [
    ("JOHN SMITH", ("John", "Smith")),
    ("John Smith", ("John", "Smith")),
    ("MARY ELLEN JONES", ("Mary", "Ellen Jones")),       # 3 tokens → first + rest
    ("MARY E JONES", ("Mary", "E Jones")),
    ("SMITH", ("", "Smith")),                             # 1 token → last only
    ("", ("", "")),                                       # empty → both empty
    ("  John   Smith  ", ("John", "Smith")),              # whitespace collapse
    ("SMITH JOHN TR", ("Smith", "John Tr")),              # assessor trustee encoding
    # ~2.3% of Cook County non-LLC owner_name strings carry a trailing
    # unit number bleeding in from the unit column ('JOHN ALDEN MORRIS
    # 812'). Tracerfy normal-mode misses when the last name carries a
    # number, so strip the trailing all-digit token.
    ("JOHN ALDEN MORRIS 812", ("John", "Alden Morris")),
    ("JUDITH RITHOLZ 1906", ("Judith", "Ritholz")),
    ("LORI M WOLFSON 2902", ("Lori", "M Wolfson")),
    # Keep ambiguous 2-token cases as-is: 'SMITH 812' could in principle
    # be a real (if unusual) last name. Only strip when we still have
    # 2+ tokens left after the strip.
    ("SMITH 812", ("Smith", "812")),
    # Don't touch digit tokens in the middle of the name — only trailing.
    ("JOHN 123 SMITH", ("John", "123 Smith")),
])
def test_split_owner_name(raw, expected):
    assert split_owner_name(raw) == expected


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


class _ScriptedProvider:
    """Stub provider that returns a queued sequence of results, recording
    each call's kwargs. Lets fallback tests assert on call order + payload."""
    name = "stub"
    cost_per_lookup_usd = 0.10

    def __init__(self, results: list[EnrichmentResult]):
        self.results = list(results)
        self.calls: list[dict] = []

    def lookup(self, **kwargs):
        self.calls.append(kwargs)
        if not self.results:
            raise AssertionError("provider called more times than scripted")
        return self.results.pop(0)


def _seed_non_llc_parcel(db):
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO parcels(pin, owner_name, mail_address, zip_code, is_llc) "
            "VALUES ('14192080160000', 'Resurrection Cov Ch', "
            "'3901 N MARSHFIELD AVE', '60613', 0)"
        )
        conn.commit()


def _run_enrich(db, pin="14192080160000", provider=None):
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        return _enrich_one_pin(conn, job_id=None, pin=pin, provider=provider)


def test_normal_hit_does_not_trigger_advanced_fallback(tmp_path):
    """If normal mode returns a hit (status='success' with contacts),
    advanced mode is NOT called — single $0.10, single result row."""
    db = tmp_path / "t.db"
    init_db(db)
    _seed_non_llc_parcel(db)
    hit = EnrichmentResult(
        contacts=[EnrichmentContact(
            value="x@y.com", kind="email",
            confidence_pct=None, source_label="stub:email:rank-1:via=Owner",
        )],
        raw_response_json="{}", cost_usd=0.10,
        provider="stub", status="success", error_message=None,
    )
    provider = _ScriptedProvider([hit])
    _run_enrich(db, provider=provider)
    assert len(provider.calls) == 1, "no fallback when first call hits"
    # First call was normal mode (carried first/last names)
    assert provider.calls[0].get("owner_first_name") == "Resurrection"


def test_normal_no_match_triggers_advanced_fallback(tmp_path):
    """Normal-mode no_match auto-falls-back to advanced mode (address-only).
    Saves both enrichment_results rows for audit transparency. Returns the
    advanced result so the endpoint sees the contacts."""
    db = tmp_path / "t.db"
    init_db(db)
    _seed_non_llc_parcel(db)
    miss = EnrichmentResult(
        contacts=[], raw_response_json="{}", cost_usd=0.0,
        provider="stub", status="no_match", error_message=None,
    )
    hit = EnrichmentResult(
        contacts=[EnrichmentContact(
            value="real-resident@gmail.com", kind="email",
            confidence_pct=None,
            source_label="stub:email:rank-1:via=Actual Resident",
        )],
        raw_response_json="{}", cost_usd=0.10,
        provider="stub", status="success", error_message=None,
    )
    provider = _ScriptedProvider([miss, hit])
    result = _run_enrich(db, provider=provider)
    assert len(provider.calls) == 2, "fallback fires when first call misses"
    # First call was normal (had names); second was advanced (no names)
    assert provider.calls[0].get("owner_first_name") == "Resurrection"
    assert provider.calls[1].get("owner_first_name") in (None, "")
    assert provider.calls[1].get("owner_last_name") in (None, "")
    # Returned result is the advanced hit (so the endpoint's status check passes)
    assert result.status == "success"
    assert len(result.contacts) == 1
    # Both enrichment_results rows persisted with distinct lookup_type
    with sqlite3.connect(db) as conn:
        rows = conn.execute(
            "SELECT lookup_type, status FROM enrichment_results "
            "WHERE pin='14192080160000' ORDER BY id"
        ).fetchall()
    assert [r[0] for r in rows] == ["skip_trace_normal", "skip_trace_advanced"]
    assert [r[1] for r in rows] == ["no_match", "success"]


def test_normal_and_advanced_both_miss(tmp_path):
    """Both modes miss → 2 result rows, both no_match, no contacts persisted,
    final result is no_match so the endpoint surfaces 'no records found'."""
    db = tmp_path / "t.db"
    init_db(db)
    _seed_non_llc_parcel(db)
    miss = EnrichmentResult(
        contacts=[], raw_response_json="{}", cost_usd=0.0,
        provider="stub", status="no_match", error_message=None,
    )
    provider = _ScriptedProvider([miss, miss])
    result = _run_enrich(db, provider=provider)
    assert len(provider.calls) == 2
    assert result.status == "no_match"
    assert result.contacts == []
    with sqlite3.connect(db) as conn:
        n_contacts = conn.execute(
            "SELECT COUNT(*) FROM contacts WHERE pin='14192080160000'"
        ).fetchone()[0]
        n_results = conn.execute(
            "SELECT COUNT(*) FROM enrichment_results WHERE pin='14192080160000'"
        ).fetchone()[0]
    assert n_contacts == 0
    assert n_results == 2


def test_normal_error_does_not_trigger_fallback(tmp_path):
    """status='error' is a different failure mode (bad payload, auth, etc.)
    — falling back wouldn't help and would just spam errors. Only no_match
    triggers fallback."""
    db = tmp_path / "t.db"
    init_db(db)
    _seed_non_llc_parcel(db)
    err = EnrichmentResult(
        contacts=[], raw_response_json='{"city":["required"]}', cost_usd=0.0,
        provider="stub", status="error",
        error_message='HTTP 400: {"city":["required"]}',
    )
    provider = _ScriptedProvider([err])
    result = _run_enrich(db, provider=provider)
    assert len(provider.calls) == 1, "errors are not retried as advanced"
    assert result.status == "error"


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
