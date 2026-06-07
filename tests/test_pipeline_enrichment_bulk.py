from __future__ import annotations
import json
import sqlite3
import threading
from pathlib import Path
import pytest
from pipeline.db import init_db
from pipeline.enrichment import (
    BudgetCap, EnrichmentContact, EnrichmentResult,
    create_enrichment_job, run_bulk_enrichment,
)


@pytest.fixture
def seeded_db(tmp_path: Path) -> Path:
    db = tmp_path / "t.db"
    init_db(db)
    with sqlite3.connect(db) as conn:
        conn.executemany(
            "INSERT INTO parcels(pin, owner_name, mail_address, is_llc) "
            "VALUES (?, ?, ?, ?)",
            [
                ("14000000000001", "John Smith",       "111 Main St",  0),
                ("14000000000002", "Acme LLC",         "222 Main St",  1),
                ("14000000000003", "Already Enriched", "333 Main St",  0),
            ],
        )
        conn.execute(
            "INSERT INTO contacts(pin, email, source) "
            "VALUES ('14000000000003', 'existing@x.com', 'manual')"
        )
        conn.commit()
    return db


class StubSkipProvider:
    """Records every lookup call so tests can assert on mode + args."""
    name = "stub"
    cost_per_lookup_usd = 0.10
    def __init__(self):
        self.calls = []
    def lookup(self, *, mail_address, owner_first_name=None,
               owner_last_name=None, **_defaults):
        first = (owner_first_name or "").strip()
        last = (owner_last_name or "").strip()
        mode = "normal" if (first and last) else "advanced"
        self.calls.append({"mode": mode, "address": mail_address,
                           "first": first, "last": last,
                           "defaults": _defaults})
        person = f"{first} {last}".strip() or "Resident One"
        email = f"{person.lower().replace(' ', '.')}@x.com"
        return EnrichmentResult(
            contacts=[
                EnrichmentContact(value=email, kind="email",
                                  confidence_pct=None,
                                  source_label=f"stub:email:rank-1:via={person}"),
                EnrichmentContact(value="3125550100", kind="phone",
                                  confidence_pct=None,
                                  source_label=f"stub:Mobile:rank-1:via={person}"),
            ],
            raw_response_json="{}",
            cost_usd=0.10, provider=self.name,
            status="success", error_message=None,
        )


def test_run_bulk_enrichment_happy_path(seeded_db):
    """Three pins: one human owner (normal mode), one LLC (advanced mode),
    one already-enriched (skipped)."""
    pins = ["14000000000001", "14000000000002", "14000000000003"]
    skip = StubSkipProvider()
    budget = BudgetCap(soft_daily_usd=100.0, hard_per_run_usd=100.0)

    def conn_factory():
        c = sqlite3.connect(seeded_db)
        c.row_factory = sqlite3.Row
        return c

    with conn_factory() as conn:
        job_id = create_enrichment_job(conn, pins)
        conn.commit()

    run_bulk_enrichment(
        conn_factory=conn_factory, job_id=job_id, pin_list=pins,
        provider=skip, budget=budget,
    )

    with conn_factory() as conn:
        job = conn.execute(
            "SELECT * FROM enrichment_jobs WHERE id=?", (job_id,)
        ).fetchone()
        assert job["status"] == "complete"

        assert len(skip.calls) == 2
        modes = sorted(c["mode"] for c in skip.calls)
        assert modes == ["advanced", "normal"]

        contacts_1 = conn.execute(
            "SELECT * FROM contacts WHERE pin='14000000000001'"
        ).fetchall()
        assert {c["email"] for c in contacts_1 if c["email"]} == {"john.smith@x.com"}
        assert {c["phone"] for c in contacts_1 if c["phone"]} == {"3125550100"}

        contacts_2 = conn.execute(
            "SELECT * FROM contacts WHERE pin='14000000000002'"
        ).fetchall()
        assert {c["email"] for c in contacts_2 if c["email"]} == {"resident.one@x.com"}

        contacts_3 = conn.execute(
            "SELECT * FROM contacts WHERE pin='14000000000003'"
        ).fetchall()
        assert len(contacts_3) == 1
        assert contacts_3[0]["email"] == "existing@x.com"

        pin_rows = conn.execute(
            "SELECT pin, status FROM enrichment_job_pins WHERE job_id=?",
            (job_id,)
        ).fetchall()
        statuses = {r["pin"]: r["status"] for r in pin_rows}
        assert statuses == {
            "14000000000001": "done",
            "14000000000002": "done",
            "14000000000003": "skipped",
        }


def test_run_bulk_enrichment_resumes_from_checkpoint(seeded_db):
    """Start a job, mark one pin done, re-run → only pending pins re-processed."""
    pins = ["14000000000001", "14000000000002"]
    skip = StubSkipProvider()
    budget = BudgetCap(soft_daily_usd=100.0, hard_per_run_usd=100.0)

    def conn_factory():
        c = sqlite3.connect(seeded_db)
        c.row_factory = sqlite3.Row
        return c

    with conn_factory() as conn:
        job_id = create_enrichment_job(conn, pins)
        conn.execute(
            "INSERT INTO enrichment_job_pins(job_id, pin, status) "
            "VALUES (?, ?, 'done')", (job_id, "14000000000001"))
        conn.commit()

    run_bulk_enrichment(
        conn_factory=conn_factory, job_id=job_id, pin_list=pins,
        provider=skip, budget=budget,
    )

    assert len(skip.calls) == 1
    assert skip.calls[0]["mode"] == "advanced"


def test_run_bulk_enrichment_pauses_on_budget(seeded_db):
    """Hard per-run cap trips → job marked paused, not complete."""
    pins = ["14000000000001", "14000000000002"]
    skip = StubSkipProvider()
    budget = BudgetCap(soft_daily_usd=100.0, hard_per_run_usd=0.01)

    def conn_factory():
        c = sqlite3.connect(seeded_db)
        c.row_factory = sqlite3.Row
        return c

    with conn_factory() as conn:
        job_id = create_enrichment_job(conn, pins)
        conn.commit()

    run_bulk_enrichment(
        conn_factory=conn_factory, job_id=job_id, pin_list=pins,
        provider=skip, budget=budget,
    )

    with conn_factory() as conn:
        job = conn.execute(
            "SELECT * FROM enrichment_jobs WHERE id=?", (job_id,)
        ).fetchone()
        assert job["status"] == "paused"
        assert "budget" in (job["paused_reason"] or "").lower()
