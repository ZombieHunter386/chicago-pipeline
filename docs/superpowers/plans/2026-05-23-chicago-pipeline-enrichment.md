# Skip-Trace Enrichment + Multi-Address Cadence — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Scope marker:** Phase A — local only, gated behind `FEATURE_OUTREACH` like the existing cadence work. Phase B (Railway migration, multi-user gating) is a separate plan.

> **Provider (resolved + live-tested 2026-05-23):** Single provider, two modes — Tracerfy instant lookup at `POST https://tracerfy.com/v1/api/trace/lookup/`. Normal mode (`find_owner: false` + first/last names) when `parcels.is_llc=0`; advanced mode (`find_owner: true` + address only) when `parcels.is_llc=1`. Both cost 5 credits ≈ $0.10/hit. CompanyData / OpenCorporates / other LLC-pierce providers NOT used — advanced mode covers LLC parcels at the same cost. Single credential: `TRACERFY_API_KEY` env var (already in `.env`). Both real fixtures (normal + advanced, anonymized) staged at `/tmp/tracerfy_normal_anon.json` and `/tmp/tracerfy_advanced_anon.json` — T6 subagent will copy them into `tests/fixtures/`.

**Goal:** Build per-parcel + bulk skip-trace enrichment, LLC one-level pierce, multi-row contact UI, BCC-fanout in the cadence engine, and Gmail auto-bounce detection on top of the shipped 7-touch cadence. Aligns with the spec at `docs/superpowers/specs/2026-05-23-skip-trace-enrichment-design.md`.

**Architecture:** Provider abstraction in `pipeline/enrichment.py` — single `EnrichmentProvider` protocol with two-mode `lookup()` method (normal vs advanced based on whether owner first/last names are supplied). Concrete adapter in `pipeline/enrichment_providers/tracerfy.py`. Bulk jobs run in a background thread with per-pin checkpointing in `enrichment_job_pins`. Bounce detection polls Gmail via launchd on a 60-min cycle plus inline-after-send best-effort. Per-row `contacts.dead` + `contacts.wrong_person` flags let the cadence engine BCC every alive address on every touch.

**Tech Stack:** Existing — Flask 3, vanilla JS, SQLite (with WAL enabled), PyYAML, pytest, Google API Python client (already in requirements.txt for Gmail send). Two new HTTP clients (provider-dependent — `httpx` or `requests`; this plan uses `requests` to match the existing codebase). No new infrastructure.

---

## Spec → file map

| Spec section | New/modified file(s) |
|---|---|
| Schema migrations (4 contact cols, 3 tables, WAL) | Modify: `pipeline/db.py`; Test: `tests/test_pipeline_db_migrations.py` |
| Provider interfaces + pure helpers | Create: `pipeline/enrichment.py`; Test: `tests/test_pipeline_enrichment.py` |
| Tracerfy adapter (both modes) | Create: `pipeline/enrichment_providers/__init__.py`, `pipeline/enrichment_providers/tracerfy.py`; Test: `tests/test_enrichment_providers_tracerfy.py`; Fixtures: `tests/fixtures/tracerfy_normal.json`, `tests/fixtures/tracerfy_advanced.json` |
| Bulk job runner + checkpoint resume | Modify: `pipeline/enrichment.py`; Test: `tests/test_pipeline_enrichment_bulk.py` |
| Budget cap | Create: `config/enrichment.yaml`; Modify: `pipeline/enrichment.py`; Test: same |
| Cadence engine — BCC fanout + alive filter | Modify: `pipeline/cadence.py`, `pipeline/outreach.py`; Test: `tests/test_pipeline_cadence.py`, `tests/test_pipeline_outreach.py` |
| Bounce poller | Create: `pipeline/bounce_poller.py`; Test: `tests/test_pipeline_bounce_poller.py` + fixtures `tests/fixtures/bounces/*.eml` |
| Routes (lookup, bulk, job-status, dead/wrong, send w/ to_list) | Modify: `webapp/routes.py`, `webapp/app.py`; Test: `tests/test_webapp_enrichment_routes.py`, existing `tests/test_webapp_outreach_routes.py` |
| Multi-row contact UI | Modify: `webapp/static/js/outreach.js`, `webapp/static/css/style.css` |
| Bulk-trace button + progress bar | Modify: `webapp/static/js/list.js`, `webapp/templates/index.html`, `webapp/static/css/style.css` |
| Touch-1+ BCC checkbox modal | Modify: `webapp/static/js/outreach.js`, `webapp/static/css/style.css` |
| Launchd installer for bounce poller | Create: `scripts/install_bounce_poller_launchd.sh`, `scripts/com.chicagopipeline.bouncepoller.plist.template`; Modify: `README.md` |

---

## Task ordering and dependency notes

- T1 (schema) unblocks everything downstream — must land first.
- T2 (interfaces + pure helpers) unblocks T3–T8.
- T3, T4, T5 (cadence + outreach changes) are independent and can run in any order after T2.
- T6 (Tracerfy adapter) — fixtures already captured + anonymized (staged at /tmp/), can run after T2. **T7 deleted** (no separate LLC-pierce provider needed).
- T8 (bulk job runner) depends on T2 + T6.
- T9 (bounce poller) depends on T1 (bounce_poll_state table) and T2 (provider stubs for testing).
- T10 (routes) depends on T2, T6, T8.
- T11–T13 (UI) depend on T10.
- T14 (launchd installer) depends on T9.
- T15 (manual smoke + final README) is the last task.

Order: T1 → T2 → T3 → T4 → T5 → T6 → T8 → T9 → T10 → T11 → T12 → T13 → T14 → T15. (T7 removed.)

Each task ends in a single commit. After T15 the branch is ready for code review + merge.

---

## Task 1: Schema migrations + WAL

**Files:**
- Modify: `chicago-pipeline/pipeline/db.py`
- Create: `chicago-pipeline/tests/test_pipeline_db_migrations.py`

- [ ] **Step 1: Read the existing db.py to find the migration block**

Run: `grep -n "_LATER_COLUMNS\|CREATE TABLE\|ALTER TABLE\|init_db" pipeline/db.py`

Locate the function `init_db` and the `_LATER_COLUMNS` list (added in PR #2 for the cadence migration). All new ALTERs will follow the same idempotent pattern.

- [ ] **Step 2: Write the failing migration test**

Create `tests/test_pipeline_db_migrations.py`:

```python
from __future__ import annotations
import sqlite3
from pathlib import Path
import pytest
from pipeline.db import init_db


@pytest.fixture
def fresh_db(tmp_path: Path) -> Path:
    db = tmp_path / "test.db"
    init_db(db)
    return db


def test_contacts_has_dead_and_wrong_person_columns(fresh_db: Path):
    with sqlite3.connect(fresh_db) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(contacts)")}
    assert {"dead", "wrong_person", "confidence_pct",
            "enrichment_source", "related_person_name",
            "dead_at", "dead_reason"} <= cols


def test_enrichment_results_table_exists(fresh_db: Path):
    with sqlite3.connect(fresh_db) as conn:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "enrichment_results" in names
    assert "enrichment_jobs" in names
    assert "enrichment_job_pins" in names
    assert "bounce_poll_state" in names


def test_wal_mode_enabled(fresh_db: Path):
    with sqlite3.connect(fresh_db) as conn:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal"


def test_init_db_idempotent(fresh_db: Path):
    # Running init_db again on an existing DB must not error
    # (ALTER TABLE ADD COLUMN raises if column exists).
    init_db(fresh_db)
    init_db(fresh_db)


def test_existing_contacts_rows_get_default_dead_false(tmp_path):
    """Simulate a DB that predates the migration: insert a contact, then
    migrate, then verify dead defaults to 0 (not NULL)."""
    db = tmp_path / "pre.db"
    init_db(db)
    with sqlite3.connect(db) as conn:
        # Force-delete the new columns to simulate pre-migration state
        # (we can't actually drop columns in old SQLite, so we test the
        # ALTER DEFAULT behavior directly):
        conn.execute(
            "INSERT INTO parcels(pin, taxpayer_name, mail_address) "
            "VALUES ('14321010010000', 'TEST OWNER', '123 MAIN ST')"
        )
        conn.execute(
            "INSERT INTO contacts(pin, email, source) "
            "VALUES ('14321010010000', 'test@example.com', 'manual')"
        )
        conn.commit()
        row = conn.execute(
            "SELECT dead, wrong_person FROM contacts WHERE pin='14321010010000'"
        ).fetchone()
    assert row == (0, 0)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_pipeline_db_migrations.py -v`
Expected: all FAIL (columns/tables don't exist yet).

- [ ] **Step 4: Add columns + tables + WAL to db.py**

In `pipeline/db.py`, find the `_LATER_COLUMNS` list and add the new contacts columns. Then in `init_db`, after the existing `CREATE TABLE` block, add the new tables. Finally, enable WAL at the top of `init_db`.

```python
# At the top of init_db, right after `with sqlite3.connect(...) as conn:`:
conn.execute("PRAGMA journal_mode=WAL")

# Add to the _LATER_COLUMNS dict (or equivalent list):
_LATER_COLUMNS["contacts"] = [
    ("dead", "BOOLEAN DEFAULT 0"),
    ("wrong_person", "BOOLEAN DEFAULT 0"),
    ("confidence_pct", "INTEGER"),
    ("enrichment_source", "TEXT"),
    ("related_person_name", "TEXT"),
    ("dead_at", "TIMESTAMP"),
    ("dead_reason", "TEXT"),
]

# After the existing CREATE TABLE statements, add:
conn.execute("""
CREATE TABLE IF NOT EXISTS enrichment_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pin TEXT NOT NULL,
    job_id INTEGER,
    provider TEXT NOT NULL,
    lookup_type TEXT NOT NULL,
    query_name TEXT NOT NULL,
    query_mail_address TEXT,
    raw_response_json TEXT NOT NULL,
    cost_usd REAL NOT NULL,
    status TEXT NOT NULL,
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)""")
conn.execute("CREATE INDEX IF NOT EXISTS idx_enrichment_results_pin ON enrichment_results(pin)")
conn.execute("CREATE INDEX IF NOT EXISTS idx_enrichment_results_job ON enrichment_results(job_id)")

conn.execute("""
CREATE TABLE IF NOT EXISTS enrichment_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pin_list_json TEXT NOT NULL,
    status TEXT NOT NULL,
    paused_reason TEXT,
    total_cost_usd REAL DEFAULT 0.0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP
)""")

conn.execute("""
CREATE TABLE IF NOT EXISTS enrichment_job_pins (
    job_id INTEGER NOT NULL,
    pin TEXT NOT NULL,
    status TEXT NOT NULL,
    error_message TEXT,
    PRIMARY KEY (job_id, pin)
)""")

conn.execute("""
CREATE TABLE IF NOT EXISTS bounce_poll_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_message_id TEXT,
    last_polled_at TIMESTAMP
)""")
# Seed the single-row state table.
conn.execute("INSERT OR IGNORE INTO bounce_poll_state(id) VALUES (1)")
```

If the project's existing migration pattern uses a different idiom (e.g., a numbered list of migration callables rather than `_LATER_COLUMNS`), adapt to that pattern — the new ALTERs and CREATEs are the same regardless.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_pipeline_db_migrations.py -v`
Expected: all 5 PASS.

- [ ] **Step 6: Run the full test suite to confirm no regressions**

Run: `pytest -q`
Expected: 344 + 5 = 349 passing.

- [ ] **Step 7: Commit**

```bash
git add pipeline/db.py tests/test_pipeline_db_migrations.py
git commit -m "feat(enrichment): schema additions for skip-trace + WAL"
```

---

## Task 2: Enrichment interfaces + pure helpers

**Files:**
- Create: `chicago-pipeline/pipeline/enrichment.py`
- Create: `chicago-pipeline/pipeline/enrichment_providers/__init__.py` (empty)
- Create: `chicago-pipeline/tests/test_pipeline_enrichment.py`

- [ ] **Step 1: Write the failing tests for pure helpers**

Create `tests/test_pipeline_enrichment.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_pipeline_enrichment.py -v`
Expected: ImportError / module not found.

- [ ] **Step 3: Implement pipeline/enrichment.py**

Create `pipeline/enrichment.py`:

```python
"""Skip-trace enrichment — pure helpers + provider interface.

Single provider (Tracerfy) with two modes:
  - Normal mode: supply owner_first_name + owner_last_name → find that
    specific person at the address.
  - Advanced mode: omit names → find anyone the provider associates with
    the address. Used for LLC-owned parcels.

The orchestrator and the provider adapter live in this module and in
pipeline/enrichment_providers/ respectively. Adding a new provider is a
one-file change in pipeline/enrichment_providers/.

LLC detection is NOT a helper here — the parcels.is_llc column populated
by the data pipeline at ingest time is the source of truth. There's no
LLC-pierce step because Tracerfy advanced mode covers LLC-owned parcels
directly at the same per-hit cost.
"""
from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Protocol


# ---------- Pure helpers ----------

def split_owner_name(raw: str) -> tuple[str, str]:
    """Split assessor `owner_name` into (first, last) for Tracerfy normal mode.

    Assessor names come in shouty caps and varied formats:
      - 'JOHN SMITH'        → ('John', 'Smith')
      - 'MARY ELLEN JONES'  → ('Mary', 'Ellen Jones')  (first token + rest)
      - 'SMITH'             → ('', 'Smith')             (one token → last)
      - 'SMITH JOHN TR'     → ('Smith', 'John Tr')      (trustee suffix kept)

    Capitalization: title-cased so Tracerfy doesn't reject for casing. The
    one-token-only case returns first='' (Tracerfy may still hit via the
    last_name alone — and the cost of a miss is $0 anyway).
    """
    s = re.sub(r"\s+", " ", (raw or "").strip())
    if not s:
        return ("", "")
    parts = s.split(" ", 1)
    if len(parts) == 1:
        return ("", parts[0].title())
    first, rest = parts
    return (first.title(), rest.title())


def alive_emails_for_parcel(contacts: list[dict]) -> list[str]:
    """Returns emails from contact rows where dead=0 and wrong_person=0."""
    out = []
    for c in contacts:
        if c.get("email") and not c.get("dead") and not c.get("wrong_person"):
            out.append(c["email"])
    return out


def alive_phones_for_parcel(contacts: list[dict]) -> list[str]:
    out = []
    for c in contacts:
        if c.get("phone") and not c.get("dead") and not c.get("wrong_person"):
            out.append(c["phone"])
    return out


# ---------- Data types ----------

@dataclass(frozen=True)
class EnrichmentContact:
    value: str                  # email address or phone number
    kind: str                   # 'email' | 'phone'
    confidence_pct: int | None  # 0..100 if provider reports; Tracerfy → None
    source_label: str           # e.g. 'tracerfy:email:rank-1:via=Jane Doe'


@dataclass(frozen=True)
class EnrichmentResult:
    contacts: list[EnrichmentContact]
    raw_response_json: str
    cost_usd: float
    provider: str
    status: str                 # 'success' | 'no_match' | 'error'
    error_message: str | None


# ---------- Provider protocol ----------

class EnrichmentProvider(Protocol):
    name: str
    cost_per_lookup_usd: float

    def lookup(
        self,
        *,
        mail_address: str,
        owner_first_name: str | None = None,
        owner_last_name: str | None = None,
    ) -> EnrichmentResult:
        """Returns surfaced contacts for a parcel.

        When owner_first_name AND owner_last_name are supplied (both
        truthy), the provider uses normal-mode by-name lookup. When EITHER
        is empty/None, the provider falls through to advanced-mode
        address-only lookup. This lets the orchestrator pass through
        whatever it has without branching on mode itself.
        """
        ...
```

Create empty `pipeline/enrichment_providers/__init__.py`:

```python
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_pipeline_enrichment.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add pipeline/enrichment.py pipeline/enrichment_providers/__init__.py tests/test_pipeline_enrichment.py
git commit -m "feat(enrichment): provider interface + pure helpers"
```

---

## Task 3: Cadence engine — BCC fanout + alive filter

**Files:**
- Modify: `chicago-pipeline/pipeline/cadence.py`
- Modify: `chicago-pipeline/tests/test_pipeline_cadence.py`

The existing cadence engine takes a single `contact` dict. We're adding multi-contact support without breaking the existing single-contact signature.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pipeline_cadence.py`:

```python
def test_next_due_with_multiple_contacts_at_least_one_alive_email():
    """When at least one email is alive, the engine returns the email touch
    as available. The recipient list is computed separately by the caller."""
    cadence = {"sequence": [
        {"touch": 1, "day_offset": 0, "channel": "email",
         "template": "t1", "requires": "email"},
    ]}
    outreach_rows = []
    contacts = [
        {"email": "a@x.com", "dead": 1, "wrong_person": 0},  # dead
        {"email": "b@x.com", "dead": 0, "wrong_person": 0},  # alive
    ]
    from pipeline.cadence import next_due_touches_for_parcel
    due = next_due_touches_for_parcel(
        cadence_config=cadence, outreach_rows=outreach_rows,
        contacts=contacts, parcel_mail_address="123 Main",
        today="2026-05-23",
    )
    assert len(due) == 1
    assert due[0]["touch"] == 1


def test_next_due_with_multiple_contacts_all_dead_emails():
    cadence = {"sequence": [
        {"touch": 1, "day_offset": 0, "channel": "email",
         "template": "t1", "requires": "email"},
    ]}
    contacts = [
        {"email": "a@x.com", "dead": 1, "wrong_person": 0},
        {"email": "b@x.com", "dead": 0, "wrong_person": 1},
    ]
    from pipeline.cadence import next_due_touches_for_parcel
    due = next_due_touches_for_parcel(
        cadence_config=cadence, outreach_rows=[],
        contacts=contacts, parcel_mail_address="123 Main",
        today="2026-05-23",
    )
    # Today's behavior was to return the touch with available=False when no
    # email is on file. Same outcome: the touch is surfaced but unsendable.
    assert len(due) == 1
    assert due[0]["touch"] == 1
    # The caller will see alive_emails_for_parcel(contacts) == [] and render
    # '(no email)' in the sequence row.


def test_next_due_signature_accepts_either_contact_or_contacts():
    """Backwards compat: callers may still pass `contact=<single dict>`."""
    cadence = {"sequence": [
        {"touch": 1, "day_offset": 0, "channel": "email",
         "template": "t1", "requires": "email"},
    ]}
    from pipeline.cadence import next_due_touches_for_parcel
    due = next_due_touches_for_parcel(
        cadence_config=cadence, outreach_rows=[],
        contact={"email": "a@x.com", "dead": 0, "wrong_person": 0},
        parcel_mail_address="123 Main", today="2026-05-23",
    )
    assert len(due) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_pipeline_cadence.py -v -k multiple_contacts`
Expected: FAIL.

- [ ] **Step 3: Modify next_due_touches_for_parcel signature**

In `pipeline/cadence.py`, change the function signature and the email/phone availability check:

```python
def next_due_touches_for_parcel(
    *,
    cadence_config: dict,
    outreach_rows: list[dict],
    contact: dict | None = None,          # backwards-compat
    contacts: list[dict] | None = None,   # new: multiple rows
    parcel_mail_address: str | None,
    today: str,
) -> list[dict]:
    """Returns the list of touches that are currently due for this parcel.

    Accepts either `contact` (single row, legacy) or `contacts` (list of
    rows, new). At least one alive email is required to satisfy
    requires='email'; same for phone. Dead rows (dead=1 or wrong_person=1)
    are filtered out before the requires check.
    """
    if contacts is None:
        contacts = [contact] if contact else []

    from pipeline.enrichment import alive_emails_for_parcel, alive_phones_for_parcel
    has_email = bool(alive_emails_for_parcel(contacts))
    has_phone = bool(alive_phones_for_parcel(contacts))
    # ... rest of function uses has_email / has_phone in place of
    # `contact.get('email')` / `contact.get('phone')` checks
```

- [ ] **Step 4: Run cadence tests to verify the new tests pass + old tests still pass**

Run: `pytest tests/test_pipeline_cadence.py -v`
Expected: all PASS (including the existing tests that pass `contact={}`).

- [ ] **Step 5: Update all callers in webapp/routes.py**

Run: `grep -n "next_due_touches_for_parcel\|all_due_touches" webapp/routes.py pipeline/`

Replace the existing `contact=dict(contact) if contact else None` with `contacts=[dict(c) for c in contact_rows]` where `contact_rows` is fetched as `SELECT * FROM contacts WHERE pin=?` (not `LIMIT 1`).

Also update `pipeline/cadence.py:all_due_touches` to fetch all contact rows per pin, not just the first.

- [ ] **Step 6: Run full test suite**

Run: `pytest -q`
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add pipeline/cadence.py webapp/routes.py tests/test_pipeline_cadence.py
git commit -m "feat(cadence): per-row alive filter for multi-contact parcels"
```

---

## Task 4: Outreach module — to_list parameter for BCC fanout

**Files:**
- Modify: `chicago-pipeline/pipeline/outreach.py`
- Modify: `chicago-pipeline/pipeline/gmail_client.py`
- Modify: `chicago-pipeline/tests/test_pipeline_outreach.py`
- Modify: `chicago-pipeline/tests/test_pipeline_gmail_client.py`

The `gmail_client.send_email` function currently accepts a single `to` parameter. We extend it with `bcc: list[str]`.

- [ ] **Step 1: Write the failing tests for gmail_client**

Append to `tests/test_pipeline_gmail_client.py` (or create if missing):

```python
def test_send_email_with_bcc_includes_bcc_header(monkeypatch, tmp_path):
    """send_email accepts bcc=list[str] and includes it in the MIME message."""
    from pipeline import gmail_client
    captured = {}

    class FakeService:
        def users(self):
            class U:
                def messages(self):
                    class M:
                        def send(self_inner, userId, body):
                            captured["body"] = body
                            class Exec:
                                def execute(self_e):
                                    return {"id": "fake-id", "threadId": "fake-thread"}
                            return Exec()
                    return M()
            return U()

    monkeypatch.setattr(gmail_client, "_build_service", lambda _: FakeService())
    result = gmail_client.send_email(
        token_path=tmp_path / "token.json",
        sender="me@example.com", to="me@example.com",
        bcc=["a@x.com", "b@y.com"],
        subject="hi", body="hello",
    )
    assert result["id"] == "fake-id"
    # The raw RFC822 message is base64-url-safe encoded in body["raw"].
    import base64
    raw = base64.urlsafe_b64decode(captured["body"]["raw"]).decode()
    assert "Bcc: a@x.com, b@y.com" in raw
    assert "To: me@example.com" in raw
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_pipeline_gmail_client.py -v -k bcc`
Expected: FAIL (TypeError: unexpected keyword 'bcc').

- [ ] **Step 3: Extend gmail_client.send_email with bcc parameter**

In `pipeline/gmail_client.py`, modify `send_email`:

```python
def send_email(
    *,
    token_path: Path,
    sender: str,
    to: str,
    subject: str,
    body: str,
    bcc: list[str] | None = None,
) -> dict:
    """Send via Gmail API. Returns {'id': ..., 'threadId': ...} on success.

    bcc: optional list of recipient addresses to send via BCC. The visible
    To: header is always `sender` per BCC-only convention; the actual
    delivery goes to `to` (typically the sender's own inbox) plus every
    address in bcc."""
    # Build MIME
    from email.mime.text import MIMEText
    msg = MIMEText(body)
    msg["To"] = to
    msg["From"] = sender
    msg["Subject"] = subject
    if bcc:
        msg["Bcc"] = ", ".join(bcc)
    # ... (existing service + send logic unchanged)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_pipeline_gmail_client.py -v -k bcc`
Expected: PASS.

- [ ] **Step 5: Run the full outreach test suite to verify no regressions**

Run: `pytest tests/test_pipeline_outreach.py tests/test_pipeline_gmail_client.py -v`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add pipeline/gmail_client.py tests/test_pipeline_gmail_client.py
git commit -m "feat(outreach): gmail_client.send_email accepts bcc list"
```

---

## Task 5: Budget cap

**Files:**
- Create: `chicago-pipeline/config/enrichment.yaml`
- Modify: `chicago-pipeline/pipeline/enrichment.py`
- Modify: `chicago-pipeline/tests/test_pipeline_enrichment.py`

- [ ] **Step 1: Create the config**

Create `config/enrichment.yaml`:

```yaml
# Enrichment provider config + budget caps.
# Soft cap → UI confirmation prompt. Hard cap → bulk job auto-pauses.
# Hard cap is sized to allow a 20-pin bulk button at $0.10/hit ($2.00)
# with $0.50 headroom.
budget:
  soft_daily_usd: 5.00
  hard_per_run_usd: 2.50

# Provider selection — change here if you swap providers.
# The adapter file must exist at pipeline/enrichment_providers/<name>.py
# and export a get_provider() function returning the adapter instance.
skip_trace_provider: tracerfy
```

- [ ] **Step 2: Write the failing budget tests**

Append to `tests/test_pipeline_enrichment.py`:

```python
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
```

- [ ] **Step 3: Implement BudgetCap**

Append to `pipeline/enrichment.py`:

```python
class BudgetExceeded(Exception):
    pass


@dataclass(frozen=True)
class BudgetCap:
    soft_daily_usd: float
    hard_per_run_usd: float

    def would_exceed_soft(self, conn, *, additional_cost: float) -> bool:
        row = conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0.0) AS s FROM enrichment_results "
            "WHERE created_at >= date('now')"
        ).fetchone()
        spent_today = row["s"] if hasattr(row, "keys") else row[0]
        return (spent_today + additional_cost) > self.soft_daily_usd

    def check_or_raise(self, conn, *, job_id: int, additional_cost: float) -> None:
        row = conn.execute(
            "SELECT COALESCE(total_cost_usd, 0.0) AS c FROM enrichment_jobs "
            "WHERE id = ?", (job_id,),
        ).fetchone()
        spent_this_run = row["c"] if hasattr(row, "keys") else row[0]
        if (spent_this_run + additional_cost) > self.hard_per_run_usd:
            raise BudgetExceeded(
                f"Hard per-run cap of ${self.hard_per_run_usd:.2f} would be "
                f"exceeded (run spent ${spent_this_run:.2f}, "
                f"additional ${additional_cost:.2f})"
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_pipeline_enrichment.py -v -k budget`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add config/enrichment.yaml pipeline/enrichment.py tests/test_pipeline_enrichment.py
git commit -m "feat(enrichment): config + BudgetCap with daily soft + per-run hard"
```

---

## Task 6: Tracerfy provider adapter (normal + advanced modes)

**Files:**
- Create: `chicago-pipeline/pipeline/enrichment_providers/tracerfy.py`
- Create: `chicago-pipeline/tests/test_enrichment_providers_tracerfy.py`
- Create: `chicago-pipeline/tests/fixtures/tracerfy_normal.json`
- Create: `chicago-pipeline/tests/fixtures/tracerfy_advanced.json`

**Pre-flight (already done by Hunter, fixtures staged at /tmp/):** Two real API responses captured + anonymized in this session — normal mode (find_owner=false with supplied name) and advanced mode (find_owner=true address-only). Both confirmed 5 credits/hit ≈ $0.10. The fixtures live at `/tmp/tracerfy_normal_anon.json` and `/tmp/tracerfy_advanced_anon.json`. **Step 1 below copies them into `tests/fixtures/`.**

- [ ] **Step 1: Copy the anonymized fixtures into the repo**

```bash
mkdir -p tests/fixtures
cp /tmp/tracerfy_normal_anon.json tests/fixtures/tracerfy_normal.json
cp /tmp/tracerfy_advanced_anon.json tests/fixtures/tracerfy_advanced.json
```

Verify shape (both fixtures should match the documented schema from <https://www.tracerfy.com/skip-tracing-api-documentation/>):

```bash
python3 -c "
import json
for f in ['normal', 'advanced']:
    d = json.load(open(f'tests/fixtures/tracerfy_{f}.json'))
    print(f'{f}: hit={d[\"hit\"]}, persons={d[\"persons_count\"]}, credits={d[\"credits_deducted\"]}')
"
```

Expected:
```
normal: hit=True, persons=1, credits=5
advanced: hit=True, persons=3, credits=5
```

- [ ] **Step 2: Write the failing adapter tests**

Create `tests/test_enrichment_providers_tracerfy.py`:

```python
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
    # All contacts should be tagged with their person's full_name
    assert all("via=" in c.source_label for c in result.contacts)


def test_parse_advanced_response_returns_multiple_persons_grouped():
    p = TracerfyProvider(api_key="fake-key")
    raw = json.loads(FIXTURE_ADVANCED.read_text())
    result = p._parse_response(raw)
    assert result.status == "success"
    # Fixture has 3 persons; each contact is tagged with its person's name
    persons_seen = set()
    for c in result.contacts:
        # source_label format: 'tracerfy:Mobile:rank-1:via=Jane Doe'
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
    # Only deceased → no_match (we still got charged 5 credits but the
    # adapter reports cost_usd=0 since we got nothing usable).
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
    # Both names omitted
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
    """Single-token assessor names (just 'SMITH') trigger advanced mode."""
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
    # Apt suffix is preserved in street (Tracerfy's documented behavior is to
    # match better without apt; the orchestrator-level decision to strip apt
    # belongs in T8, not here).
    ("123 Main St Apt 4, Chicago, IL 60601",
     {"address": "123 Main St Apt 4", "city": "Chicago", "state": "IL", "zip": "60601"}),
])
def test_parse_mail_address(raw_addr, expected):
    assert parse_mail_address(raw_addr) == expected
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_enrichment_providers_tracerfy.py -v`
Expected: ImportError.

- [ ] **Step 4: Implement the adapter**

Create `pipeline/enrichment_providers/tracerfy.py`:

```python
"""Tracerfy skip-trace adapter — two-mode instant lookup.

Endpoint: https://tracerfy.com/v1/api/trace/lookup/
Docs: https://www.tracerfy.com/skip-tracing-api-documentation/

Modes:
  - Normal (find_owner=false): supply first_name + last_name, returns the
    specific person at the address. Used when parcels.is_llc=0.
  - Advanced (find_owner=true): no name, returns the humans at the address
    regardless of public records. Used when parcels.is_llc=1.

Both modes cost 5 credits per hit (≈ $0.10), 0 credits on miss. Rate
limit: 500 RPM per user. Live-tested 2026-05-23.

The adapter chooses the mode based on whether both first_name AND
last_name are supplied (and non-empty). If either is missing, advanced
mode is used. This makes the caller's branching simple: pass through
whatever you've parsed; if you can't parse a name, the adapter does the
right thing.
"""
from __future__ import annotations
import json
import re
from dataclasses import dataclass
import requests
from pipeline.enrichment import (
    EnrichmentContact, EnrichmentResult, EnrichmentProvider,
)


TRACERFY_ENDPOINT = "https://tracerfy.com/v1/api/trace/lookup/"


ZIP_RE = re.compile(r"\b(\d{5}(?:-\d{4})?)\b")
STATE_RE = re.compile(r"\b([A-Z]{2})\b")


def parse_mail_address(raw: str) -> dict:
    """Best-effort parse of assessor freeform mail_address into the
    structured fields Tracerfy requires. Returns dict with keys
    address/city/state/zip; missing parts become empty strings."""
    if not raw:
        return {"address": "", "city": "", "state": "", "zip": ""}
    s = raw.strip()
    zip_m = ZIP_RE.search(s)
    zip_code = zip_m.group(1) if zip_m else ""
    if zip_m:
        s = s[:zip_m.start()] + s[zip_m.end():]
    state_m = STATE_RE.search(s)
    state = state_m.group(1) if state_m else ""
    if state_m:
        s = s[:state_m.start()] + s[state_m.end():]
    s = re.sub(r",\s*,", ",", s).strip().strip(",").strip()
    if "," in s:
        street, city = s.rsplit(",", 1)
        return {"address": street.strip(), "city": city.strip(),
                "state": state, "zip": zip_code}
    return {"address": s.strip(), "city": "", "state": state, "zip": zip_code}


@dataclass
class TracerfyProvider:
    api_key: str
    name: str = "tracerfy"
    cost_per_lookup_usd: float = 0.10  # 5 credits × $0.02/credit, confirmed live

    def lookup(
        self,
        *,
        mail_address: str,
        owner_first_name: str | None = None,
        owner_last_name: str | None = None,
    ) -> EnrichmentResult:
        parsed = parse_mail_address(mail_address or "")
        first = (owner_first_name or "").strip()
        last = (owner_last_name or "").strip()
        # Mode selection: advanced if either name is blank.
        use_advanced = not (first and last)
        body = {
            "address": parsed["address"],
            "city": parsed["city"],
            "state": parsed["state"] or "IL",  # default IL for Chicago pipeline
            "zip": parsed["zip"],
            "find_owner": use_advanced,
        }
        if not use_advanced:
            body["first_name"] = first
            body["last_name"] = last
        try:
            resp = requests.post(
                TRACERFY_ENDPOINT,
                json=body,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                timeout=30,
            )
        except requests.RequestException as e:
            return EnrichmentResult(
                contacts=[], raw_response_json=json.dumps({"error": str(e)}),
                cost_usd=0.0, provider=self.name,
                status="error", error_message=str(e),
            )
        if resp.status_code != 200:
            return EnrichmentResult(
                contacts=[], raw_response_json=resp.text,
                cost_usd=0.0, provider=self.name,
                status="error",
                error_message=f"HTTP {resp.status_code}: {resp.text[:200]}",
            )
        try:
            raw = resp.json()
        except ValueError:
            return EnrichmentResult(
                contacts=[], raw_response_json=resp.text,
                cost_usd=0.0, provider=self.name,
                status="error", error_message="non-JSON response",
            )
        return self._parse_response(raw)

    def _parse_response(self, raw: dict) -> EnrichmentResult:
        if not raw.get("hit") or not raw.get("persons"):
            return EnrichmentResult(
                contacts=[], raw_response_json=json.dumps(raw),
                cost_usd=0.0,  # Tracerfy charges 0 credits on miss
                provider=self.name, status="no_match", error_message=None,
            )
        contacts: list[EnrichmentContact] = []
        live_persons = 0
        for person in raw["persons"]:
            if person.get("deceased"):
                continue
            live_persons += 1
            full_name = person.get("full_name") or (
                f"{person.get('first_name', '')} {person.get('last_name', '')}".strip()
            )
            for ph in person.get("phones") or []:
                num = ph.get("number")
                if not num:
                    continue
                rank = ph.get("rank")
                ph_type = ph.get("type", "Phone")
                label = f"tracerfy:{ph_type}"
                if rank is not None:
                    label += f":rank-{rank}"
                if full_name:
                    label += f":via={full_name}"
                contacts.append(EnrichmentContact(
                    value=num, kind="phone",
                    confidence_pct=None, source_label=label,
                ))
            for em in person.get("emails") or []:
                addr = em.get("email")
                if not addr:
                    continue
                rank = em.get("rank")
                label = "tracerfy:email"
                if rank is not None:
                    label += f":rank-{rank}"
                if full_name:
                    label += f":via={full_name}"
                contacts.append(EnrichmentContact(
                    value=addr, kind="email",
                    confidence_pct=None, source_label=label,
                ))
        if live_persons == 0:
            return EnrichmentResult(
                contacts=[], raw_response_json=json.dumps(raw),
                cost_usd=0.0, provider=self.name,
                status="no_match", error_message="all_deceased",
            )
        return EnrichmentResult(
            contacts=contacts, raw_response_json=json.dumps(raw),
            cost_usd=self.cost_per_lookup_usd,
            provider=self.name, status="success", error_message=None,
        )


def get_provider(api_key: str) -> EnrichmentProvider:
    return TracerfyProvider(api_key=api_key)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_enrichment_providers_tracerfy.py -v`
Expected: all PASS.

- [ ] **Step 6: One live API call to verify the wiring (already validated 2026-05-23, optional re-check)**

The provider was live-tested in this session: 4 successful calls, 1 miss, fixtures captured. If you want to re-verify post-implementation:

```bash
source .venv/bin/activate
TRACERFY_API_KEY=$(grep TRACERFY_API_KEY .env | cut -d= -f2) python3 -c "
from pipeline.enrichment_providers.tracerfy import TracerfyProvider
p = TracerfyProvider(api_key='$TRACERFY_API_KEY'.strip())
# Advanced mode — known hit from validation
r = p.lookup(mail_address='3835 N Greenview Ave, Chicago, IL 60613')
print('status:', r.status, '| contacts:', len(r.contacts), '| cost:', r.cost_usd)
for c in r.contacts[:3]:
    print(' ', c.kind, c.value, c.source_label)
"
```

Expected: status=success, multiple contacts, cost=0.10.

- [ ] **Step 7: Commit**

```bash
git add pipeline/enrichment_providers/tracerfy.py tests/test_enrichment_providers_tracerfy.py tests/fixtures/tracerfy_normal.json tests/fixtures/tracerfy_advanced.json
git commit -m "feat(enrichment): Tracerfy two-mode skip-trace adapter"
```

---

## Task 7: [DELETED — no separate LLC-pierce provider needed]

The original plan included a CompanyData adapter to look up LLC officers. After live testing on 2026-05-23, we confirmed that Tracerfy's advanced mode (`find_owner: true`) returns the humans physically associated with an LLC-owned property at the same per-hit cost ($0.10) as a normal name+address lookup. No separate provider is needed; the LLC case is handled entirely within T6.

## Task 8: Bulk enrichment job runner

**Files:**
- Modify: `chicago-pipeline/pipeline/enrichment.py`
- Create: `chicago-pipeline/tests/test_pipeline_enrichment_bulk.py`

- [ ] **Step 1: Write the failing test for the orchestrator**

Create `tests/test_pipeline_enrichment_bulk.py`:

```python
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
        # The orchestrator picks the lookup mode based on is_llc. We test
        # both branches: human owner (is_llc=0) and LLC owner (is_llc=1).
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
        self.calls = []  # list of dicts: {mode, address, first, last}
    def lookup(self, *, mail_address, owner_first_name=None, owner_last_name=None):
        first = (owner_first_name or "").strip()
        last = (owner_last_name or "").strip()
        mode = "normal" if (first and last) else "advanced"
        self.calls.append({"mode": mode, "address": mail_address,
                           "first": first, "last": last})
        # Synthesize a person name from inputs so contacts are testable
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

        # Two API calls: one normal-mode for pin 1, one advanced-mode for pin 2.
        # Pin 3 was skipped (already had contacts) → no call.
        assert len(skip.calls) == 2
        modes = sorted(c["mode"] for c in skip.calls)
        assert modes == ["advanced", "normal"]

        # Pin 1 (human owner): contacts came from normal-mode call
        contacts_1 = conn.execute(
            "SELECT * FROM contacts WHERE pin='14000000000001'"
        ).fetchall()
        assert {c["email"] for c in contacts_1 if c["email"]} == {"john.smith@x.com"}
        assert {c["phone"] for c in contacts_1 if c["phone"]} == {"3125550100"}

        # Pin 2 (LLC owner): contacts came from advanced-mode call. The
        # related_person_name is parsed out of the source_label by the
        # orchestrator and stored in the column directly.
        contacts_2 = conn.execute(
            "SELECT * FROM contacts WHERE pin='14000000000002'"
        ).fetchall()
        # The stub fabricates "Resident One" when no name is supplied
        assert {c["email"] for c in contacts_2 if c["email"]} == {"resident.one@x.com"}

        # Pin 3 (already enriched): not touched
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
        # Pre-mark first pin as done so the runner should skip it
        conn.execute(
            "INSERT INTO enrichment_job_pins(job_id, pin, status) "
            "VALUES (?, ?, 'done')", (job_id, "14000000000001"))
        conn.commit()

    run_bulk_enrichment(
        conn_factory=conn_factory, job_id=job_id, pin_list=pins,
        provider=skip, budget=budget,
    )

    # First pin should NOT have been re-traced — only advanced-mode call for pin 2.
    assert len(skip.calls) == 1
    assert skip.calls[0]["mode"] == "advanced"


def test_run_bulk_enrichment_pauses_on_budget(seeded_db):
    """Hard per-run cap trips → job marked paused, not complete."""
    pins = ["14000000000001", "14000000000002"]
    skip = StubSkipProvider()
    # Tiny hard cap: even one lookup pushes over (0.10 > 0.01)
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_pipeline_enrichment_bulk.py -v`
Expected: FAIL (functions not defined).

- [ ] **Step 3: Implement the orchestrator + helpers**

Append to `pipeline/enrichment.py`:

```python
import json as _json
import sqlite3 as _sqlite3
from contextlib import closing


def create_enrichment_job(conn, pin_list: list[str]) -> int:
    cur = conn.execute(
        "INSERT INTO enrichment_jobs(pin_list_json, status) VALUES (?, 'running')",
        (_json.dumps(pin_list),),
    )
    return cur.lastrowid


def _has_fresh_contacts(conn, pin: str) -> bool:
    """Per the spec: any existing contact row counts as fresh; no time-decay."""
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM contacts WHERE pin=?", (pin,)
    ).fetchone()
    n = row["c"] if hasattr(row, "keys") else row[0]
    return n > 0


def _save_enrichment_result(conn, *, pin, job_id, lookup_type, query_name,
                            query_mail_address, result) -> None:
    conn.execute(
        "INSERT INTO enrichment_results(pin, job_id, provider, lookup_type, "
        "query_name, query_mail_address, raw_response_json, cost_usd, "
        "status, error_message) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (pin, job_id, result.provider, lookup_type, query_name,
         query_mail_address, result.raw_response_json, result.cost_usd,
         result.status, result.error_message),
    )
    if job_id is not None and result.cost_usd > 0:
        conn.execute(
            "UPDATE enrichment_jobs SET total_cost_usd = "
            "COALESCE(total_cost_usd, 0.0) + ? WHERE id = ?",
            (result.cost_usd, job_id),
        )


def _extract_via(source_label: str) -> str | None:
    """Pull the person's full name out of a source_label like
    'tracerfy:Mobile:rank-1:via=Jane Doe'. Returns None if no via=."""
    for part in (source_label or "").split(":"):
        if part.startswith("via="):
            name = part[len("via="):].strip()
            return name or None
    return None


def _persist_contacts(conn, pin: str, result: EnrichmentResult) -> None:
    """Insert one contacts row per surfaced email / phone. Dedup by value.

    The per-contact source_label (e.g. 'tracerfy:email:rank-1:via=Jane Doe')
    goes into enrichment_source. The person's name is extracted from
    'via=...' and stored in related_person_name so the UI can render
    'via Jane Doe' without re-parsing on every render."""
    for c in result.contacts:
        column = "email" if c.kind == "email" else "phone"
        existing = conn.execute(
            f"SELECT contact_id FROM contacts WHERE pin=? AND {column}=?",
            (pin, c.value),
        ).fetchone()
        if existing:
            continue
        related = _extract_via(c.source_label)
        conn.execute(
            f"INSERT INTO contacts(pin, {column}, source, "
            "enrichment_source, confidence_pct, related_person_name) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (pin, c.value, "enrichment", c.source_label,
             c.confidence_pct, related),
        )


def _enrich_one_pin(conn, job_id: int, pin: str,
                    provider: EnrichmentProvider) -> None:
    """Picks the lookup mode based on parcels.is_llc:
      - is_llc=0 → split owner_name into first/last and call normal mode
      - is_llc=1 → omit names, call advanced mode (address-only)

    The adapter handles the actual mode-switch internally via the
    presence/absence of owner_first_name + owner_last_name."""
    parcel = conn.execute(
        "SELECT * FROM parcels WHERE pin=?", (pin,)
    ).fetchone()
    if parcel is None:
        raise ValueError(f"pin {pin} not in parcels table")
    mail = parcel["mail_address"]
    if parcel["is_llc"]:
        result = provider.lookup(mail_address=mail)
        lookup_type = "skip_trace_advanced"
    else:
        first, last = split_owner_name(parcel["owner_name"] or "")
        result = provider.lookup(
            mail_address=mail,
            owner_first_name=first, owner_last_name=last,
        )
        lookup_type = "skip_trace_normal"
    _save_enrichment_result(
        conn, pin=pin, job_id=job_id, lookup_type=lookup_type,
        query_name=parcel["owner_name"], query_mail_address=mail,
        result=result,
    )
    _persist_contacts(conn, pin, result)


def run_bulk_enrichment(
    *,
    conn_factory,
    job_id: int,
    pin_list: list[str],
    provider: EnrichmentProvider,
    budget: BudgetCap,
) -> None:
    with closing(conn_factory()) as conn:
        for pin in pin_list:
            row = conn.execute(
                "SELECT status FROM enrichment_job_pins WHERE job_id=? AND pin=?",
                (job_id, pin),
            ).fetchone()
            if row and row["status"] in ("done", "skipped"):
                continue
            if _has_fresh_contacts(conn, pin):
                conn.execute(
                    "INSERT OR REPLACE INTO enrichment_job_pins"
                    "(job_id, pin, status) VALUES (?, ?, 'skipped')",
                    (job_id, pin),
                )
                conn.commit()
                continue
            try:
                budget.check_or_raise(
                    conn, job_id=job_id, additional_cost=provider.cost_per_lookup_usd,
                )
            except BudgetExceeded as e:
                conn.execute(
                    "UPDATE enrichment_jobs SET status='paused', paused_reason=? "
                    "WHERE id=?", (str(e), job_id),
                )
                conn.commit()
                return
            try:
                _enrich_one_pin(conn, job_id, pin, provider)
                conn.execute(
                    "INSERT OR REPLACE INTO enrichment_job_pins"
                    "(job_id, pin, status) VALUES (?, ?, 'done')",
                    (job_id, pin),
                )
                conn.commit()
            except Exception as e:
                conn.execute(
                    "INSERT OR REPLACE INTO enrichment_job_pins"
                    "(job_id, pin, status, error_message) "
                    "VALUES (?, ?, 'error', ?)",
                    (job_id, pin, str(e)),
                )
                conn.commit()
        conn.execute(
            "UPDATE enrichment_jobs SET status='complete', "
            "completed_at=CURRENT_TIMESTAMP WHERE id=?", (job_id,),
        )
        conn.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_pipeline_enrichment_bulk.py -v`
Expected: all PASS.

- [ ] **Step 5: Run the full test suite**

Run: `pytest -q`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add pipeline/enrichment.py tests/test_pipeline_enrichment_bulk.py
git commit -m "feat(enrichment): bulk job runner with per-pin checkpoint resume"
```

---

## Task 9: Bounce poller

**Files:**
- Create: `chicago-pipeline/pipeline/bounce_poller.py`
- Create: `chicago-pipeline/tests/test_pipeline_bounce_poller.py`
- Create: `chicago-pipeline/tests/fixtures/bounces/gmail_hard_bounce.eml`
- Create: `chicago-pipeline/tests/fixtures/bounces/gmail_recipient_unknown.eml`

- [ ] **Step 1: Capture two real bounce fixtures**

Send two test emails from your Gmail to addresses you know will bounce:
- An obvious typo: `nonexistent-recipient-test@gmail.com`
- A nonexistent domain: `test@nonexistent-domain-fake-12345.com`

When the mailer-daemon bouncebacks arrive, export each as `.eml` via Gmail's "Show original" → "Download original" link. Save under `tests/fixtures/bounces/`. **Anonymize:** replace your Gmail address with `me@example.com` and any real Message-IDs with `<fake-msg-id@example.com>`.

- [ ] **Step 2: Write the failing parser tests**

Create `tests/test_pipeline_bounce_poller.py`:

```python
from __future__ import annotations
from pathlib import Path
import pytest
from pipeline.bounce_poller import extract_failed_recipients


FIXTURES = Path(__file__).parent / "fixtures" / "bounces"


def test_extract_from_gmail_hard_bounce():
    body = (FIXTURES / "gmail_hard_bounce.eml").read_text()
    addresses = extract_failed_recipients(body)
    assert len(addresses) >= 1
    assert all("@" in a for a in addresses)


def test_extract_from_gmail_recipient_unknown():
    body = (FIXTURES / "gmail_recipient_unknown.eml").read_text()
    addresses = extract_failed_recipients(body)
    assert len(addresses) >= 1


def test_extract_returns_empty_for_non_bounce():
    addresses = extract_failed_recipients("Hello, just a normal email.\n")
    assert addresses == []


def test_extract_dedup():
    body = """
    Final-Recipient: rfc822; bouncy@example.com
    Final-Recipient: rfc822; bouncy@example.com
    """
    assert extract_failed_recipients(body) == ["bouncy@example.com"]
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_pipeline_bounce_poller.py -v`
Expected: ImportError.

- [ ] **Step 4: Implement the parser + poller orchestrator**

Create `pipeline/bounce_poller.py`:

```python
"""Gmail bounce poller. Scans recent mailer-daemon messages, extracts
failed recipients via Final-Recipient headers, marks matching contact
rows dead. Idempotent — uses bounce_poll_state.last_message_id to
process only new messages."""
from __future__ import annotations
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

# Final-Recipient per RFC 3464:
#   Final-Recipient: rfc822; user@example.com
FINAL_RECIPIENT_RE = re.compile(
    r"^Final-Recipient:\s*rfc822;\s*([^\s<>]+)",
    re.IGNORECASE | re.MULTILINE,
)
# Fallback: address in angle brackets in the human-readable body, e.g.
#   "The following message to <user@example.com> was undeliverable."
ANGLE_ADDR_RE = re.compile(
    r"<([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})>",
)


def extract_failed_recipients(body: str) -> list[str]:
    """Returns deduped list of addresses parsed from a bounceback message."""
    seen = []
    for addr in FINAL_RECIPIENT_RE.findall(body or ""):
        a = addr.strip().lower()
        if a and a not in seen:
            seen.append(a)
    if not seen:
        # Fallback heuristic — only run if Final-Recipient is missing.
        for addr in ANGLE_ADDR_RE.findall(body or ""):
            a = addr.strip().lower()
            if a and a not in seen and not a.endswith("@googlemail.com") \
                    and not a.endswith("@google.com"):
                seen.append(a)
    return seen


def mark_addresses_dead(conn: sqlite3.Connection, addresses: list[str],
                        reason: str = "bounce") -> int:
    """Flip dead=1 for any contacts row whose email matches an address.
    Returns count of rows updated."""
    if not addresses:
        return 0
    placeholders = ",".join("?" for _ in addresses)
    cur = conn.execute(
        f"UPDATE contacts SET dead=1, dead_at=CURRENT_TIMESTAMP, dead_reason=? "
        f"WHERE LOWER(email) IN ({placeholders}) AND dead=0",
        (reason, *addresses),
    )
    conn.commit()
    return cur.rowcount


def poll_once(
    *,
    db_path: Path,
    gmail_token_path: Path,
    fetch_messages_fn=None,    # injectable for testing
) -> dict:
    """Fetches new mailer-daemon messages, parses, marks dead.
    Returns {'messages_processed': N, 'addresses_flipped': M}."""
    from pipeline import gmail_client
    fetch = fetch_messages_fn or gmail_client.fetch_mailer_daemon_messages

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        state = conn.execute(
            "SELECT last_message_id FROM bounce_poll_state WHERE id=1"
        ).fetchone()
        last_id = state["last_message_id"] if state else None

        messages = fetch(token_path=gmail_token_path, since_message_id=last_id)
        total_flipped = 0
        highest_id = last_id
        for msg_id, body in messages:
            addrs = extract_failed_recipients(body)
            total_flipped += mark_addresses_dead(conn, addrs)
            highest_id = msg_id  # Gmail API returns newest-first; track latest

        if highest_id and highest_id != last_id:
            conn.execute(
                "UPDATE bounce_poll_state SET last_message_id=?, "
                "last_polled_at=CURRENT_TIMESTAMP WHERE id=1",
                (highest_id,),
            )
            conn.commit()
    return {
        "messages_processed": len(messages),
        "addresses_flipped": total_flipped,
    }
```

- [ ] **Step 5: Implement gmail_client.fetch_mailer_daemon_messages**

Append to `pipeline/gmail_client.py`:

```python
def fetch_mailer_daemon_messages(
    *, token_path: Path, since_message_id: str | None = None,
) -> list[tuple[str, str]]:
    """Returns [(message_id, decoded_body), ...] for messages from
    mailer-daemon@googlemail.com or @google.com that are newer than
    since_message_id (Gmail API list is newest-first; we stop when we
    see the marker)."""
    service = _build_service(token_path)
    resp = service.users().messages().list(
        userId="me",
        q="from:mailer-daemon newer_than:7d",
        maxResults=50,
    ).execute()
    out = []
    for item in resp.get("messages", []):
        msg_id = item["id"]
        if since_message_id and msg_id == since_message_id:
            break
        full = service.users().messages().get(
            userId="me", id=msg_id, format="raw",
        ).execute()
        import base64
        raw = base64.urlsafe_b64decode(full["raw"]).decode("utf-8", errors="replace")
        out.append((msg_id, raw))
    return out
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_pipeline_bounce_poller.py -v`
Expected: all PASS.

- [ ] **Step 7: Add a poll_once integration test with a stub fetch_fn**

Append to `tests/test_pipeline_bounce_poller.py`:

```python
def test_poll_once_flips_matching_contacts(tmp_path):
    from pipeline.db import init_db
    from pipeline.bounce_poller import poll_once
    db = tmp_path / "t.db"
    init_db(db)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO parcels(pin, taxpayer_name) VALUES ('14000000000001', 'X')"
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
    # Substitute bouncy@example.com into the fixture if not present
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
```

Add `import sqlite3` at the top of the test file if not already imported.

- [ ] **Step 8: Run tests to verify the integration test passes**

Run: `pytest tests/test_pipeline_bounce_poller.py -v`
Expected: all PASS.

- [ ] **Step 9: Commit**

```bash
git add pipeline/bounce_poller.py pipeline/gmail_client.py tests/test_pipeline_bounce_poller.py tests/fixtures/bounces/
git commit -m "feat(enrichment): Gmail bounce poller flips dead addresses"
```

---

## Task 10: Routes

**Files:**
- Modify: `chicago-pipeline/webapp/app.py`
- Modify: `chicago-pipeline/webapp/routes.py`
- Create: `chicago-pipeline/tests/test_webapp_enrichment_routes.py`

This task wires the providers + bulk runner + bounce poller behind HTTP endpoints. All routes are gated by `FEATURE_OUTREACH` like the existing outreach block.

- [ ] **Step 1: Wire provider construction into app.py**

In `webapp/app.py`, in `create_app`, after the existing Gmail config block:

```python
# Enrichment provider wiring — reads config/enrichment.yaml + env for the
# provider API keys, hands the constructed providers to the routes via
# app.config so tests can inject stubs.
from pathlib import Path
import yaml
enrichment_cfg_path = Path("config/enrichment.yaml")
if enrichment_cfg_path.exists() and feature_outreach:
    enrichment_cfg = yaml.safe_load(enrichment_cfg_path.read_text())
    app.config["ENRICHMENT_CFG"] = enrichment_cfg
    import os
    tracerfy_key = os.environ.get("TRACERFY_API_KEY")
    if tracerfy_key:
        from pipeline.enrichment_providers.tracerfy import TracerfyProvider
        app.config["ENRICHMENT_SKIP_PROVIDER"] = TracerfyProvider(api_key=tracerfy_key)
    from pipeline.enrichment import BudgetCap
    budget_cfg = enrichment_cfg.get("budget", {})
    app.config["ENRICHMENT_BUDGET"] = BudgetCap(
        soft_daily_usd=float(budget_cfg.get("soft_daily_usd", 5.00)),
        hard_per_run_usd=float(budget_cfg.get("hard_per_run_usd", 2.50)),
    )
```

Add `TRACERFY_API_KEY=` placeholder line to `.env.example`.

- [ ] **Step 2: Write the failing route tests**

Create `tests/test_webapp_enrichment_routes.py`:

```python
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
        def lookup(self, *, mail_address, owner_first_name=None, owner_last_name=None):
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
    # First call adds a contact
    client.post("/api/enrichment/lookup/14000000000001")
    # Second call should refuse (no auto re-enrich per spec)
    r = client.post("/api/enrichment/lookup/14000000000001")
    assert r.status_code == 409


def test_post_enrichment_bulk_kicks_off_job(app):
    client = app.test_client()
    r = client.post("/api/enrichment/bulk", json={"pins": ["14000000000001"]})
    assert r.status_code == 202
    job_id = r.get_json()["job_id"]
    # Poll for completion
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
    # Find the contact id
    with app.app_context():
        from webapp.routes import _conn
        with _conn() as conn:
            row = conn.execute(
                "SELECT contact_id FROM contacts WHERE pin='14000000000001' LIMIT 1"
            ).fetchone()
    cid = row[0]
    r = client.post(f"/api/contacts/{cid}/dead")
    assert r.status_code == 200
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
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_webapp_enrichment_routes.py -v`
Expected: route handlers don't exist → 404.

- [ ] **Step 4: Add the new routes**

In `webapp/routes.py`, inside the `if feature_outreach:` block, add:

```python
@app.post("/api/enrichment/lookup/<pin>")
def api_enrichment_lookup(pin: str):
    if not pin.isdigit() or len(pin) != 14:
        abort(400, "invalid pin")
    provider = app.config.get("ENRICHMENT_SKIP_PROVIDER")
    budget = app.config.get("ENRICHMENT_BUDGET")
    if not provider:
        abort(503, "Enrichment provider not configured (set TRACERFY_API_KEY)")
    with closing(_conn()) as conn:
        parcel = conn.execute("SELECT * FROM parcels WHERE pin=?", (pin,)).fetchone()
        if parcel is None:
            abort(404)
        from pipeline.enrichment import _has_fresh_contacts, _enrich_one_pin
        if _has_fresh_contacts(conn, pin):
            abort(409, "parcel already has contacts (no auto re-enrich)")
        # Single-parcel lookup uses the soft daily cap only — the hard
        # per-run cap is bulk-job-scoped. If over soft and the user hasn't
        # passed ?confirm=true, surface 429 so the UI can prompt.
        if budget.would_exceed_soft(
            conn, additional_cost=provider.cost_per_lookup_usd,
        ) and request.args.get("confirm") != "true":
            abort(429, "soft daily cap would be exceeded; resend with ?confirm=true to override")
        try:
            _enrich_one_pin(conn, job_id=None, pin=pin, provider=provider)
            conn.commit()
        except Exception as e:
            abort(500, str(e))
        # Return the newly persisted contacts
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM contacts WHERE pin=?", (pin,)
        )]
    return jsonify({"status": "success", "contacts": rows})


@app.post("/api/enrichment/bulk")
def api_enrichment_bulk():
    data = request.get_json(silent=True) or {}
    pins = data.get("pins") or []
    if not isinstance(pins, list) or not pins:
        abort(400, "pins must be a non-empty list")
    for p in pins:
        if not (isinstance(p, str) and p.isdigit() and len(p) == 14):
            abort(400, f"invalid pin: {p}")
    provider = app.config.get("ENRICHMENT_SKIP_PROVIDER")
    budget = app.config.get("ENRICHMENT_BUDGET")
    if not provider:
        abort(503, "Enrichment provider not configured (set TRACERFY_API_KEY)")
    db_path = Path(app.config["DB_PATH"])
    def conn_factory():
        c = sqlite3.connect(db_path)
        c.row_factory = sqlite3.Row
        return c
    from pipeline.enrichment import create_enrichment_job, run_bulk_enrichment
    with closing(conn_factory()) as conn:
        job_id = create_enrichment_job(conn, pins)
        conn.commit()
    import threading
    threading.Thread(
        target=run_bulk_enrichment,
        kwargs=dict(conn_factory=conn_factory, job_id=job_id, pin_list=pins,
                    provider=provider, budget=budget),
        daemon=True,
    ).start()
    return jsonify({"job_id": job_id}), 202


@app.get("/api/enrichment/job/<int:job_id>")
def api_enrichment_job_status(job_id: int):
    with closing(_conn()) as conn:
        job = conn.execute(
            "SELECT * FROM enrichment_jobs WHERE id=?", (job_id,)
        ).fetchone()
        if job is None:
            abort(404)
        pins = conn.execute(
            "SELECT pin, status, error_message FROM enrichment_job_pins WHERE job_id=?",
            (job_id,)
        ).fetchall()
    return jsonify({
        "job_id": job_id,
        "status": job["status"],
        "paused_reason": job["paused_reason"],
        "total_cost_usd": job["total_cost_usd"],
        "pins": [dict(p) for p in pins],
    })


@app.post("/api/contacts/<int:contact_id>/dead")
def api_contact_mark_dead(contact_id: int):
    with closing(_conn()) as conn:
        cur = conn.execute(
            "UPDATE contacts SET dead=1, dead_at=CURRENT_TIMESTAMP, "
            "dead_reason='manual' WHERE contact_id=?",
            (contact_id,),
        )
        if cur.rowcount == 0:
            abort(404)
        conn.commit()
    return jsonify({"ok": True})


@app.post("/api/contacts/<int:contact_id>/wrong-person")
def api_contact_mark_wrong_person(contact_id: int):
    with closing(_conn()) as conn:
        cur = conn.execute(
            "UPDATE contacts SET wrong_person=1 WHERE contact_id=?",
            (contact_id,),
        )
        if cur.rowcount == 0:
            abort(404)
        conn.commit()
    return jsonify({"ok": True})
```

- [ ] **Step 5: Modify api_outreach_send to accept to_list**

In the existing `api_outreach_send` function in `webapp/routes.py`, change the validation block:

```python
to = data.get("to") or ""
to_list = data.get("to_list") or []
# Backwards-compat: if only `to` was supplied, treat it as a 1-item list.
if to and not to_list:
    to_list = [to]
if not to_list:
    abort(400, "to or to_list is required")
for addr in to_list:
    if not EMAIL_RE.match(addr):
        abort(400, f"invalid recipient email: {addr}")

# Validate every address is an alive contact for this pin.
with closing(_conn()) as conn:
    alive = {r["email"] for r in conn.execute(
        "SELECT email FROM contacts WHERE pin=? AND dead=0 AND wrong_person=0 "
        "AND email IS NOT NULL", (pin,)
    )}
    bad = [a for a in to_list if a not in alive]
    if bad:
        abort(400, f"addresses are not alive contacts: {', '.join(bad)}")
```

Replace the existing `to=to,` keyword in the `gmail_client.send_email(...)` call with:

```python
to=sender,                  # visible To: header is the sender per BCC convention
bcc=to_list,                # actual delivery
```

And in the contact-upsert block, change to iterate over `to_list`:

```python
contact_ids = [
    outreach_module.upsert_contact(conn, pin=pin, email=addr, source="manual")
    for addr in to_list
]
# Pass the first contact_id to create_outreach_record (the column is non-null);
# the BCC fanout is a property of the touch, not the contact-row.
cid = contact_ids[0]
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_webapp_enrichment_routes.py -v`
Expected: all PASS.

- [ ] **Step 7: Run the full test suite**

Run: `pytest -q`
Expected: all green.

- [ ] **Step 8: Commit**

```bash
git add webapp/app.py webapp/routes.py tests/test_webapp_enrichment_routes.py .env.example
git commit -m "feat(enrichment): routes for lookup, bulk, job status, dead/wrong, BCC send"
```

---

## Task 11: Multi-row contact UI

**Files:**
- Modify: `chicago-pipeline/webapp/static/js/outreach.js`
- Modify: `chicago-pipeline/webapp/static/css/style.css`

- [ ] **Step 1: Locate the existing renderContactSection in outreach.js**

Run: `grep -n "renderContactSection\|outreach-email-input\|outreach-compose-btn" webapp/static/js/outreach.js`

The existing function shows a single email input + Compose button. We replace it with a stacked-rows layout.

- [ ] **Step 2: Replace renderContactSection with multi-row layout**

In `webapp/static/js/outreach.js`, replace the body of `renderContactSection(parcel, data)` with:

```javascript
function renderContactSection(parcel, data) {
  const el = document.createElement('div');
  el.className = 'detail-section';
  const contacts = data.contacts || (data.contact ? [data.contact] : []);
  const gmailStatus = data.gmail_connected
    ? '<span class="outreach-gmail-status-connected">✓ Gmail connected</span>'
    : '<a href="/api/oauth/start" class="outreach-connect-link">Connect Gmail</a>';

  const rowsHtml = contacts.map(c => renderContactRow(c)).join('') ||
    '<div style="font-size:12px; color:#8b949e;">No contacts on file. Click Trace owner or Add manually.</div>';

  el.innerHTML = `
    <h3>Contact <span class="outreach-gmail-status">${gmailStatus}</span></h3>
    <div class="contacts-rows">${rowsHtml}</div>
    <div class="contacts-actions">
      <button type="button" class="btn btn-primary" id="trace-owner-btn">+ Trace owner</button>
      <button type="button" class="btn" id="add-manual-btn">+ Add manually</button>
      <button type="button" class="btn btn-primary" id="outreach-compose-btn"
        title="${contacts.length ? '' : 'Add a contact first'}"
        ${contacts.length ? '' : 'disabled'}>Compose ▸</button>
    </div>
  `;

  el.querySelectorAll('[data-mark-dead]').forEach(b => {
    b.addEventListener('click', () => markContactDead(b.dataset.markDead, parcel.pin));
  });
  el.querySelectorAll('[data-mark-wrong]').forEach(b => {
    b.addEventListener('click', () => markContactWrong(b.dataset.markWrong, parcel.pin));
  });
  el.querySelector('#trace-owner-btn').addEventListener('click', () => traceOwner(parcel.pin));
  el.querySelector('#add-manual-btn').addEventListener('click', () => openAddManual(parcel.pin));
  el.querySelector('#outreach-compose-btn').addEventListener('click', () =>
    openComposeForNextDue(parcel, data));
  return el;
}

// Turn a per-contact source_label like 'tracerfy:email:rank-1' or
// 'tracerfy:Mobile:rank-2' into a human-readable meta line. Falls back
// to the raw value if there's no ':' (e.g. legacy 'manual' rows).
function humanizeSourceLabel(raw) {
  if (!raw) return '';
  if (!raw.includes(':')) return raw;
  const parts = raw.split(':');
  const provider = parts[0];
  const middle = parts.slice(1, -1).filter(p => p && p !== 'email');
  const last = parts[parts.length - 1];
  const rankBit = last.startsWith('rank-') ? last.replace('rank-', 'rank ') : last;
  // Order: rank · provider · type — matches the spec's UI mockup.
  return [rankBit, provider, ...middle].filter(Boolean).join(' · ');
}

function renderContactRow(c) {
  const kindIcon = c.email ? '✉' : '☎';
  const value = c.email || c.phone;
  // Some providers (future-BatchData, manual) supply confidence_pct; Tracerfy
  // doesn't, only rank (encoded in enrichment_source). Render either one.
  const meta = c.confidence_pct != null
    ? `${c.confidence_pct}% · ${c.enrichment_source || ''}`
    : humanizeSourceLabel(c.enrichment_source || c.source || '');
  const related = c.related_person_name
    ? ` <span class="contact-related">via ${escapeHtml(c.related_person_name)}</span>` : '';
  const dead = c.dead ? ' contact-row-dead' : '';
  const wrong = c.wrong_person ? ' contact-row-wrong' : '';
  return `
    <div class="contact-row${dead}${wrong}">
      <span class="contact-icon">${kindIcon}</span>
      <span class="contact-value">${escapeHtml(value || '')}</span>
      <span class="contact-meta">${escapeHtml(meta)}${related}</span>
      <div class="contact-actions">
        ${c.dead ? '<span class="contact-tag">dead</span>' :
          `<button type="button" class="btn btn-sm" data-mark-dead="${c.contact_id}">Mark dead</button>`}
        ${c.wrong_person ? '<span class="contact-tag">wrong</span>' :
          `<button type="button" class="btn btn-sm" data-mark-wrong="${c.contact_id}">Wrong person</button>`}
      </div>
    </div>
  `;
}

async function markContactDead(cid, pin) {
  try {
    await fetch(`/api/contacts/${cid}/dead`, {method: 'POST'});
    showToast('Marked dead', 'success');
    window.dispatchEvent(new CustomEvent('outreach:refresh', {detail: {pin}}));
  } catch (_) { showToast("Couldn't mark dead", 'error'); }
}

async function markContactWrong(cid, pin) {
  try {
    await fetch(`/api/contacts/${cid}/wrong-person`, {method: 'POST'});
    showToast('Marked wrong person', 'success');
    window.dispatchEvent(new CustomEvent('outreach:refresh', {detail: {pin}}));
  } catch (_) { showToast("Couldn't mark wrong person", 'error'); }
}

async function traceOwner(pin) {
  try {
    const r = await fetch(`/api/enrichment/lookup/${pin}`, {method: 'POST'});
    if (!r.ok) {
      const text = await r.text();
      throw new Error(text);
    }
    showToast('Trace complete', 'success');
    window.dispatchEvent(new CustomEvent('outreach:refresh', {detail: {pin}}));
  } catch (e) {
    showToast(`Trace failed: ${e.message}`, 'error');
  }
}

function openAddManual(pin) {
  const value = prompt("Email or phone to add (manual entry):");
  if (!value || !value.trim()) return;
  const isEmail = value.includes('@');
  const body = isEmail
    ? {pin, email: value.trim(), source: 'manual'}
    : {pin, phone: value.trim(), source: 'manual'};
  fetch('/api/contacts/upsert', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body),
  }).then(r => {
    if (!r.ok) throw new Error('save failed');
    showToast('Added', 'success');
    window.dispatchEvent(new CustomEvent('outreach:refresh', {detail: {pin}}));
  }).catch(_ => showToast("Couldn't add", 'error'));
}

function openComposeForNextDue(parcel, data) {
  const nextDue = data.sequence && data.sequence.next_due;
  if (!nextDue) { showToast('No touch is currently due', 'info'); return; }
  const touchNum = nextDue.touch;
  const channel = nextDue.channel;
  if (channel === 'email') {
    window.__outreachOpenCompose(parcel, data.contacts || [],
                                  data.sender_address, touchNum);
  } else if (channel === 'phone') {
    openPhoneModal(parcel, data.contacts || [], touchNum);
  } else if (channel === 'mail') {
    openMailModal(parcel, touchNum);
  }
}
```

Note: `api_parcel_outreach` in `webapp/routes.py` currently returns `contact` (singular). Update it to also return `contacts` (plural) — keep `contact` for backwards compat:

```python
contacts = [dict(r) for r in conn.execute(
    "SELECT * FROM contacts WHERE pin = ?", (pin,)
).fetchall()]
contact = contacts[0] if contacts else None
# ...
return jsonify({
    ...,
    "contact": contact,        # legacy
    "contacts": contacts,      # new
    ...,
})
```

- [ ] **Step 3: Add CSS for the new layout**

Append to `webapp/static/css/style.css`:

```css
.contacts-rows {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-bottom: 8px;
}

.contact-row {
  display: grid;
  grid-template-columns: 24px 1fr auto auto;
  gap: 8px;
  align-items: center;
  padding: 6px 8px;
  border: 1px solid #30363d;
  border-radius: 4px;
  font-size: 13px;
}

.contact-row-dead { opacity: 0.5; text-decoration: line-through; }
.contact-row-wrong { opacity: 0.5; background: #2d1f1f; }

.contact-icon { font-size: 14px; color: #8b949e; }
.contact-value { font-weight: 500; }
.contact-meta { color: #8b949e; font-size: 11px; }
.contact-related { color: #58a6ff; }
.contact-tag {
  font-size: 10px; padding: 2px 6px; background: #21262d;
  border-radius: 3px; color: #8b949e;
}

.contacts-actions {
  display: flex; gap: 8px; margin-top: 6px;
}
```

- [ ] **Step 4: Smoke-test the UI in the browser**

```bash
python -m webapp --db data/smoke.db --outreach --port 5051
```

Open <http://127.0.0.1:5051/>, pick a parcel, click Trace owner. Verify the new rows render with confidence + source. Click Mark dead — row gets struck through. Refresh page — state persists.

- [ ] **Step 5: Commit**

```bash
git add webapp/static/js/outreach.js webapp/static/css/style.css webapp/routes.py
git commit -m "feat(enrichment): multi-row contact UI with dead/wrong-person controls"
```

---

## Task 12: Bulk-trace button + progress bar

**Files:**
- Modify: `chicago-pipeline/webapp/templates/index.html`
- Modify: `chicago-pipeline/webapp/static/js/list.js`
- Modify: `chicago-pipeline/webapp/static/css/style.css`

- [ ] **Step 1: Add the bulk button to index.html**

In `webapp/templates/index.html`, inside the list-view header, add:

```html
{% if feature_outreach %}
<button type="button" id="bulk-trace-btn" class="btn btn-primary">⚡ Bulk trace top 20</button>
<div id="bulk-trace-progress" hidden>
  <span class="bulk-trace-label">Tracing: <span id="bulk-trace-done">0</span> / <span id="bulk-trace-total">0</span></span>
  <div class="bulk-trace-bar"><div id="bulk-trace-fill"></div></div>
</div>
{% endif %}
```

- [ ] **Step 2: Add the bulk-trace logic to list.js**

Append to `webapp/static/js/list.js`:

```javascript
(function () {
  const btn = document.getElementById('bulk-trace-btn');
  if (!btn) return;
  btn.addEventListener('click', async () => {
    // Top 20 of current filter view — read from the list panel's visible rows
    const visibleRows = document.querySelectorAll('#parcels-list [data-pin]');
    const pins = Array.from(visibleRows).slice(0, 20).map(r => r.dataset.pin);
    if (pins.length === 0) {
      alert('No parcels in current view'); return;
    }
    const estCost = (pins.length * 0.02).toFixed(2);
    if (!confirm(`Trace top ${pins.length} parcels (est. $${estCost})? Parcels with existing contacts are skipped.`)) return;

    const r = await fetch('/api/enrichment/bulk', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({pins}),
    });
    if (!r.ok) { alert('Bulk trace failed to start'); return; }
    const {job_id} = await r.json();
    pollProgress(job_id, pins.length);
  });

  async function pollProgress(jobId, total) {
    const prog = document.getElementById('bulk-trace-progress');
    const done = document.getElementById('bulk-trace-done');
    const totalEl = document.getElementById('bulk-trace-total');
    const fill = document.getElementById('bulk-trace-fill');
    prog.hidden = false;
    totalEl.textContent = total;
    while (true) {
      await new Promise(r => setTimeout(r, 2000));
      const r = await fetch(`/api/enrichment/job/${jobId}`);
      const data = await r.json();
      const doneCount = (data.pins || []).filter(p =>
        p.status === 'done' || p.status === 'skipped' || p.status === 'error').length;
      done.textContent = doneCount;
      fill.style.width = `${(doneCount / total * 100).toFixed(1)}%`;
      if (data.status === 'complete') {
        setTimeout(() => { prog.hidden = true; window.location.reload(); }, 1500);
        break;
      }
      if (data.status === 'paused') {
        alert(`Bulk trace paused: ${data.paused_reason}`);
        break;
      }
      if (data.status === 'failed') {
        alert('Bulk trace failed');
        break;
      }
    }
  }
})();
```

- [ ] **Step 3: Add CSS for the progress bar**

Append to `webapp/static/css/style.css`:

```css
#bulk-trace-progress {
  display: flex; gap: 8px; align-items: center; margin: 8px 0;
}
.bulk-trace-bar {
  width: 200px; height: 6px; background: #21262d; border-radius: 3px; overflow: hidden;
}
#bulk-trace-fill {
  height: 100%; background: #58a6ff; width: 0; transition: width 0.3s;
}
.bulk-trace-label { font-size: 12px; color: #8b949e; }
```

- [ ] **Step 4: Smoke-test in browser**

```bash
python -m webapp --db data/smoke.db --outreach --port 5051
```

Open the list view, click the bulk button, confirm, watch the progress bar advance, verify page reloads with new contacts.

- [ ] **Step 5: Commit**

```bash
git add webapp/templates/index.html webapp/static/js/list.js webapp/static/css/style.css
git commit -m "feat(enrichment): bulk-trace button + live progress bar"
```

---

## Task 13: Touch-1 (and every touch) BCC checkbox modal

**Files:**
- Modify: `chicago-pipeline/webapp/static/js/outreach.js`

The existing compose modal sends to a single `data.contact.email`. We replace it with a checkbox list of alive emails, all checked by default. The send POST sends `to_list` instead of `to`.

- [ ] **Step 1: Modify openComposeModal**

In `webapp/static/js/outreach.js`, change the signature to accept `contacts: list[dict]` and replace the "To" row of the modal HTML:

```javascript
async function openComposeModal(parcel, contacts, senderAddress, touchNumber) {
  // ... existing template fetch logic ...
  const aliveEmails = (contacts || []).filter(c =>
    c.email && !c.dead && !c.wrong_person);
  if (aliveEmails.length === 0) {
    alert("No alive email addresses for this parcel. Add a contact or run a trace first.");
    return;
  }
  // Build the modal — replace the existing single-email To: row with:
  const toRowHtml = `
    <div class="cm-row">
      <label class="cm-label">To (BCC, all checked by default)</label>
      <div class="bcc-checkbox-list">
        ${aliveEmails.map((c, i) => `
          <label class="bcc-checkbox">
            <input type="checkbox" data-bcc-email="${escapeHtml(c.email)}" checked>
            ${escapeHtml(c.email)}
            <span class="bcc-meta">${escapeHtml(
              c.confidence_pct != null
                ? c.confidence_pct + '% · ' + (c.enrichment_source || '')
                : humanizeSourceLabel(c.enrichment_source || c.source || '')
            )}</span>
          </label>
        `).join('')}
      </div>
    </div>
  `;
  // Replace the existing <div class="cm-row">…To…</div> block in the modal
  // HTML template with toRowHtml.

  // When Send is clicked:
  const toList = Array.from(root.querySelectorAll('[data-bcc-email]:checked'))
    .map(cb => cb.dataset.bccEmail);
  if (toList.length === 0) {
    alert("Check at least one recipient.");
    return;
  }
  const payload = {
    pin: parcel.pin,
    to_list: toList,
    subject: subjectInput.value,
    body: bodyTextarea.value,
    touch_number: touchNumber,
  };
  await sendOutreach(payload);
}
```

- [ ] **Step 2: Update the call site in renderContactSection**

The call site already passes contacts via `openComposeForNextDue(parcel, data)` from T11. Verify the call now passes `data.contacts` (not `data.contact`).

- [ ] **Step 3: Add CSS for the checkbox list**

Append to `webapp/static/css/style.css`:

```css
.bcc-checkbox-list {
  display: flex; flex-direction: column; gap: 4px;
  border: 1px solid #30363d; border-radius: 4px; padding: 8px;
  max-height: 200px; overflow-y: auto;
}
.bcc-checkbox { display: flex; gap: 8px; align-items: center; font-size: 13px; }
.bcc-meta { color: #8b949e; font-size: 11px; margin-left: auto; }
```

- [ ] **Step 4: Smoke-test**

In the browser, run a trace that produces multiple emails, click Compose on a touch-1-due parcel. Verify the modal shows all alive emails with checkboxes, all checked. Uncheck one, click Send. Network tab should show `to_list` with the unchecked address absent.

- [ ] **Step 5: Commit**

```bash
git add webapp/static/js/outreach.js webapp/static/css/style.css
git commit -m "feat(enrichment): BCC checkbox list in compose modal"
```

---

## Task 14: Launchd installer for bounce poller

**Files:**
- Create: `chicago-pipeline/scripts/install_bounce_poller_launchd.sh`
- Create: `chicago-pipeline/scripts/com.chicagopipeline.bouncepoller.plist.template`
- Modify: `chicago-pipeline/README.md`

- [ ] **Step 1: Create the plist template**

Create `scripts/com.chicagopipeline.bouncepoller.plist.template`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.chicagopipeline.bouncepoller</string>
  <key>ProgramArguments</key>
  <array>
    <string>{{PROJECT_ROOT}}/.venv/bin/python</string>
    <string>-m</string>
    <string>pipeline.bounce_poller</string>
    <string>--db</string>
    <string>{{PROJECT_ROOT}}/data/smoke.db</string>
  </array>
  <key>StartInterval</key>
  <integer>3600</integer>
  <key>RunAtLoad</key>
  <true/>
  <key>StandardOutPath</key>
  <string>{{PROJECT_ROOT}}/data/bounce_poller.log</string>
  <key>StandardErrorPath</key>
  <string>{{PROJECT_ROOT}}/data/bounce_poller.err.log</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/usr/bin:/bin:/usr/sbin:/sbin</string>
  </dict>
</dict>
</plist>
```

- [ ] **Step 2: Create the installer script**

Create `scripts/install_bounce_poller_launchd.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PLIST_NAME="com.chicagopipeline.bouncepoller.plist"
TARGET="$HOME/Library/LaunchAgents/$PLIST_NAME"

mkdir -p "$HOME/Library/LaunchAgents"
sed "s|{{PROJECT_ROOT}}|$PROJECT_ROOT|g" \
  "$PROJECT_ROOT/scripts/$PLIST_NAME.template" > "$TARGET"

launchctl unload "$TARGET" 2>/dev/null || true
launchctl load "$TARGET"

echo "Installed: $TARGET"
echo "Runs every 3600s. Logs: $PROJECT_ROOT/data/bounce_poller.{log,err.log}"
echo "Uninstall: launchctl unload $TARGET && rm $TARGET"
```

Make it executable:

```bash
chmod +x scripts/install_bounce_poller_launchd.sh
```

- [ ] **Step 3: Add CLI entry to bounce_poller.py**

Append to `pipeline/bounce_poller.py`:

```python
def _main():
    import argparse, os
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    args = parser.parse_args()
    token_path = Path(os.environ.get("GMAIL_TOKEN_PATH", "data/gmail_token.json"))
    result = poll_once(db_path=args.db, gmail_token_path=token_path)
    print(f"Bounce poll: {result['messages_processed']} messages, "
          f"{result['addresses_flipped']} addresses flipped dead")


if __name__ == "__main__":
    _main()
```

- [ ] **Step 4: Document in README**

In `README.md`, add a subsection under the existing cadence ops section:

```markdown
### Bounce poller (auto-flips dead addresses)

Runs hourly via launchd. Parses Gmail mailer-daemon bouncebacks and marks matching
contact rows as `dead`. Install once:

```bash
./scripts/install_bounce_poller_launchd.sh
```

Logs: `data/bounce_poller.log`. Uninstall: `launchctl unload ~/Library/LaunchAgents/com.chicagopipeline.bouncepoller.plist`.
```

- [ ] **Step 5: Smoke-test by running once manually**

Run: `python -m pipeline.bounce_poller --db data/smoke.db`
Expected: prints "Bounce poll: N messages, M addresses flipped dead". On a fresh inbox with no recent bounces, N=0, M=0.

- [ ] **Step 6: Commit**

```bash
git add scripts/install_bounce_poller_launchd.sh scripts/com.chicagopipeline.bouncepoller.plist.template pipeline/bounce_poller.py README.md
git commit -m "feat(enrichment): launchd installer + CLI for bounce poller"
```

---

## Task 15: Final smoke + README + PR

**Files:**
- Modify: `chicago-pipeline/README.md`
- No code changes

- [ ] **Step 1: Run the full test suite a final time**

Run: `pytest -q`
Expected: all green. Note the new total (should be ~344 + 40 new = ~384).

- [ ] **Step 2: Live-provider smoke (Tracerfy)**

Pick one real Chicago parcel from the smoke DB. Pre-flight:

```bash
# TRACERFY_API_KEY already set in .env from earlier in the project
python -m webapp --db data/smoke.db --outreach --port 5051
```

Click Trace owner on the parcel. Verify:
- Network tab shows `POST /api/enrichment/lookup/<pin>` returning 200.
- New contact rows appear with real emails/phones.
- Mode picked correctly (normal vs advanced) by inspecting `enrichment_results.lookup_type`:

```bash
sqlite3 data/smoke.db "SELECT pin, provider, lookup_type, cost_usd, status FROM enrichment_results ORDER BY id DESC LIMIT 5"
```

- [ ] **Step 3: Live-provider smoke (LLC parcel, advanced mode)**

Pick a parcel where `parcels.is_llc=1`. Click Trace owner. Verify:
- `enrichment_results.lookup_type='skip_trace_advanced'` for the new row.
- Multiple contact rows appear with `related_person_name` set (one per surfaced person from Tracerfy's advanced-mode response).
- `cost_usd=0.10` (5 credits) on the audit row.

- [ ] **Step 4: Live bulk smoke**

Apply a filter that gives ~5 parcels. Click Bulk trace top 20. Verify:
- Progress bar advances.
- `enrichment_jobs.total_cost_usd` matches your expectations.
- A pin with pre-existing contacts shows status='skipped' in `enrichment_job_pins`.

- [ ] **Step 5: Live BCC fanout smoke**

Pick a parcel with 2+ alive emails. Click Compose on touch 1. Verify:
- Checkbox list shows both emails.
- Send → check BCC line in the actual email in your inbox.

- [ ] **Step 6: Bounce poll smoke**

Send a test email via the cadence to an obviously-bouncing address (e.g., `nonexistent-test@gmail.com`). Wait for the mailer-daemon reply. Run:

```bash
python -m pipeline.bounce_poller --db data/smoke.db
```

Expected: "1 messages, 1 addresses flipped". Verify `contacts.dead` is now 1 for that address.

- [ ] **Step 7: Update README with the enrichment section**

In `README.md`, add a section:

```markdown
## Skip-trace enrichment

The "Trace owner" button on the detail panel runs a skip-trace via Tracerfy's instant-lookup API (~$0.10/hit) and stores surfaced emails + phones as multiple `contacts` rows per parcel. The "Bulk trace top 20" button enriches the top 20 of the current filter view in a background job with per-pin checkpoint resume.

Mode selection is automatic: parcels where `is_llc=0` use Tracerfy normal mode (supply first + last name); parcels where `is_llc=1` use Tracerfy advanced mode (address only — returns whoever's physically associated with the property). Both modes cost the same per hit, so the choice is purely about which has better data for the situation.

### Required env

```bash
TRACERFY_API_KEY=...            # https://tracerfy.com — sign in → dashboard → API keys
```

### Budget cap

Configured in `config/enrichment.yaml`:
- `soft_daily_usd` — UI confirmation prompt above this daily spend
- `hard_per_run_usd` — bulk jobs auto-pause above this per-run spend
```

- [ ] **Step 8: Final commit + push**

```bash
git add README.md
git commit -m "docs: README section for skip-trace enrichment"
git push -u origin enrichment
```

- [ ] **Step 9: Open PR**

```bash
gh pr create --title "feat(enrichment): skip-trace + multi-address cadence" \
  --body "$(cat <<'EOF'
## Summary

Per-parcel + bulk skip-trace enrichment, LLC one-level pierce, multi-row contacts UI, BCC-fanout in the cadence engine, and Gmail auto-bounce detection. Aligns with spec at `docs/superpowers/specs/2026-05-23-skip-trace-enrichment-design.md`.

## What landed

- Schema additions: `contacts.{dead, wrong_person, confidence_pct, enrichment_source, related_person_name, dead_at, dead_reason}` + 4 new tables + SQLite WAL.
- `pipeline/enrichment.py` — provider protocols, pure helpers (is_llc, alive_emails_for_parcel, etc), bulk runner with per-pin checkpoint resume, budget cap.
- `pipeline/enrichment_providers/tracerfy.py` — two-mode adapter (normal + advanced).
- `pipeline/bounce_poller.py` — Gmail API mailer-daemon parser, marks matching contact rows dead.
- Cadence engine accepts multi-contact parcels; BCC fanout in the compose modal.
- Multi-row contact UI with per-row dead/wrong-person buttons.
- Bulk-trace button on the list view with live progress bar.
- Launchd installer for hourly bounce poller.

## Tests

- N passing (was 344, +N new for enrichment).

## Live smoke completed

- Tracerfy lookup on one real parcel
- Advanced-mode lookup on one real LLC-owned parcel
- Bulk job on ~5 parcels
- BCC fanout on a multi-email parcel
- Bounce poll detected and flipped one dead address

## Configuration

- `TRACERFY_API_KEY` env var (single provider, both modes)
- `config/enrichment.yaml` for budget + agent blocklist

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Phase B preview (deferred)

- Provider API key moves to Railway env (`TRACERFY_API_KEY`).
- Bulk jobs run server-side (threading.Thread still fine for ~30/mo volume; Celery is overkill).
- Multi-user gating — only Hunter sees the enrichment UI.
- Bounce poller cron moves from local launchd to Railway scheduled tasks.
- Per-user provider quotas if a second user joins.

Phase B is its own plan, triggered after Phase A validates the cadence-with-fanout shape under real-world use.
