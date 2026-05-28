from __future__ import annotations
import pytest
from pipeline.enrichment import (
    split_owner_name,
    alive_emails_for_parcel,
    alive_phones_for_parcel,
    EnrichmentContact,
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
