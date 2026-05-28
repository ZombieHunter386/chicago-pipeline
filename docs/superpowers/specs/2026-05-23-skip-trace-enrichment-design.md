# Skip-Trace Enrichment + Multi-Address Cadence — Design

**Status:** Draft, 2026-05-23. Brainstormed and validated this session via live API tests. **Provider resolved 2026-05-23:** Tracerfy instant lookup ([`POST https://tracerfy.com/v1/api/trace/lookup/`](https://www.tracerfy.com/skip-tracing-api-documentation/)) — single provider, two modes:
- **Normal mode** (`find_owner: false`, supplied first_name/last_name) when `parcels.is_llc=0` and a human owner name is known
- **Advanced mode** (`find_owner: true`, address only) when `parcels.is_llc=1` — Tracerfy returns the humans physically associated with the address, regardless of whether public records can match an LLC name to a person

Live test 2026-05-23 confirmed both modes cost 5 credits per hit (~$0.10), 0 credits on miss. LLC pierce via a separate provider (CompanyData / OpenCorporates) is **not needed** — Tracerfy's address-based search covers the LLC case at the same cost.

**Goal:** Add per-parcel skip-trace enrichment + LLC one-level pierce to the existing outreach cadence. A "Trace" button on the detail panel enriches one parcel; a "Bulk trace top 20" button on the list view enriches the current filter view (skipping parcels that already have a non-expired enrichment). Surfaced emails and phones become multiple `contacts` rows per parcel. The cadence engine BCCs every alive email on every touch (1–7). Bounce detection auto-flips dead addresses via Gmail-API mailer-daemon parsing.

**Phasing:**
- **Phase A — local enrichment.** Build the provider adapter(s), schema additions, enrichment job runner with checkpoint resume, multi-row contact UI, BCC fanout in the cadence engine, Gmail bounce poller, budget cap. Runs entirely on Hunter's local Mac with the same `--outreach` gate as Phase A cadence. **This is the scope of the implementation plan that follows this spec.**
- **Phase B — Railway migration.** Provider API keys move to Railway env. Bulk jobs run server-side. Multi-user gating (already deferred in the cadence Phase B plan). Separate plan triggered after Phase A validates the cadence-with-fanout shape.

---

## Architecture

### 1. Provider abstraction

All API calls go through one interface. Implementation lives in `pipeline/enrichment_providers/tracerfy.py`. The orchestrator code never imports a provider directly — it receives one by dependency injection from the Flask app factory or CLI. Swapping providers in the future is a one-file change.

```python
# pipeline/enrichment.py — interface definitions

@dataclass(frozen=True)
class EnrichmentContact:
    """One surfaced contact (either email or phone, never both).
    Multiple of these per parcel produce multiple `contacts` rows."""
    value: str                        # email address or phone number
    kind: str                         # 'email' | 'phone'
    confidence_pct: int | None        # 0..100 if provider reports; else None
    source_label: str                 # e.g. 'tracerfy:email:rank-1'

@dataclass(frozen=True)
class EnrichmentResult:
    contacts: list[EnrichmentContact]
    raw_response_json: str            # full provider response, stored verbatim
    cost_usd: float
    provider: str                     # 'tracerfy' | 'manual' | ...
    status: str                       # 'success' | 'no_match' | 'error'
    error_message: str | None

class EnrichmentProvider(Protocol):
    name: str                         # e.g. 'tracerfy'
    cost_per_lookup_usd: float        # e.g. 0.10

    def lookup(
        self,
        *,
        mail_address: str,
        owner_first_name: str | None = None,
        owner_last_name: str | None = None,
    ) -> EnrichmentResult:
        """When owner_first_name + owner_last_name supplied → normal-mode
        skip-trace by name + address. When both None → advanced-mode lookup
        by address only (returns whoever the provider associates with the
        address)."""
```

The orchestrator picks the mode per-parcel based on `parcels.is_llc`:
- `is_llc=0` (human owner): pass `owner_first_name` + `owner_last_name` parsed from `parcels.owner_name`
- `is_llc=1` (LLC owner): omit the name params → advanced-mode address-only lookup

### 2. Schema additions

Three new tables and four new columns on `contacts`. All migrations are idempotent ALTERs / `CREATE TABLE IF NOT EXISTS` for dev-DB compatibility (matches existing pattern in `pipeline/db.py`).

```sql
-- Per-row flags so the cadence engine can skip on a per-address basis.
ALTER TABLE contacts ADD COLUMN dead BOOLEAN DEFAULT 0;
ALTER TABLE contacts ADD COLUMN wrong_person BOOLEAN DEFAULT 0;
ALTER TABLE contacts ADD COLUMN confidence_pct INTEGER;
ALTER TABLE contacts ADD COLUMN enrichment_source TEXT;
   -- 'tracerfy' | 'manual' | ...
ALTER TABLE contacts ADD COLUMN related_person_name TEXT;
   -- The full_name of the Tracerfy-returned person this contact belongs to.
   -- Advanced-mode lookups return multiple persons per parcel; this column
   -- groups the resulting contact rows so the UI can render "via Jane Doe".
   -- For manual entries, NULL.
ALTER TABLE contacts ADD COLUMN dead_at TIMESTAMP;
ALTER TABLE contacts ADD COLUMN dead_reason TEXT;
   -- 'bounce' | 'manual' | 'wrong_person'

-- Audit log of every API call. Raw responses stored verbatim so we can
-- replay logic changes against historical data without re-paying.
CREATE TABLE IF NOT EXISTS enrichment_results (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    pin             TEXT NOT NULL,
    job_id          INTEGER,          -- NULL for ad-hoc one-off lookups
    provider        TEXT NOT NULL,
    lookup_type     TEXT NOT NULL,     -- 'skip_trace_normal' | 'skip_trace_advanced'
    query_name      TEXT NOT NULL,
    query_mail_address TEXT,
    raw_response_json TEXT NOT NULL,
    cost_usd        REAL NOT NULL,
    status          TEXT NOT NULL,
    error_message   TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (pin) REFERENCES parcels(pin),
    FOREIGN KEY (job_id) REFERENCES enrichment_jobs(id)
);
-- lookup_type values in v1: 'skip_trace_normal' | 'skip_trace_advanced'.
-- A future LLC-pierce provider would introduce 'llc_pierce'; the column is
-- enum-style strings (not constrained) so adding values is a no-op.
CREATE INDEX IF NOT EXISTS idx_enrichment_results_pin ON enrichment_results(pin);
CREATE INDEX IF NOT EXISTS idx_enrichment_results_job ON enrichment_results(job_id);

-- Bulk job header.
CREATE TABLE IF NOT EXISTS enrichment_jobs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    pin_list_json   TEXT NOT NULL,
    status          TEXT NOT NULL,    -- 'running' | 'complete' | 'paused' | 'failed'
    paused_reason   TEXT,
    total_cost_usd  REAL DEFAULT 0.0,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at    TIMESTAMP
);

-- Per-pin checkpoint. Flask restart in the middle of a bulk job resumes
-- by scanning this table for rows with status='pending'.
CREATE TABLE IF NOT EXISTS enrichment_job_pins (
    job_id          INTEGER NOT NULL,
    pin             TEXT NOT NULL,
    status          TEXT NOT NULL,    -- 'pending' | 'done' | 'skipped' | 'error'
    error_message   TEXT,
    PRIMARY KEY (job_id, pin),
    FOREIGN KEY (job_id) REFERENCES enrichment_jobs(id)
);

-- Bounce-poller checkpoint. Stores the highest historyId / messageId we've
-- already processed so polling is incremental.
CREATE TABLE IF NOT EXISTS bounce_poll_state (
    id              INTEGER PRIMARY KEY CHECK (id = 1),  -- single-row table
    last_message_id TEXT,
    last_polled_at  TIMESTAMP
);
```

WAL mode is enabled in `pipeline/db.py:init_db` via `conn.execute("PRAGMA journal_mode=WAL")`. WAL is a per-DB property persisted in the SQLite file header — set once, applies forever. The change is safe to run on the existing smoke + full DBs; it just changes the journaling strategy and unlocks concurrent reads while a writer is active (what we need so the bulk-job background loop doesn't block detail-panel page loads).

### 3. Bulk enrichment job

Sync, in-process, single-thread. Runs in a Flask route handler that returns immediately with a `job_id` after kicking off a background thread (Python `threading.Thread`). The thread iterates the pin list, calling the provider per pin, writing results to SQLite as it goes. The UI polls `GET /api/enrichment/job/<id>` for progress.

**Why a thread, not a Celery/RQ worker:** matches the project's existing "Flask process is the whole app" model. The bulk run is bounded at ~$2 per click; a crash mid-run loses at most a few seconds of work, and the per-pin checkpoint table makes resume trivial. Adding a worker queue is YAGNI at this scale.

```python
def run_bulk_enrichment(
    *,
    conn_factory: Callable[[], sqlite3.Connection],
    job_id: int,
    pin_list: list[str],
    provider: EnrichmentProvider,
    budget: BudgetCap,
) -> None:
    """Runs in a background thread. Each pin checkpointed individually so
    a Flask restart can resume from where it left off."""
    with closing(conn_factory()) as conn:
        for pin in pin_list:
            row = conn.execute(
                "SELECT status FROM enrichment_job_pins WHERE job_id=? AND pin=?",
                (job_id, pin),
            ).fetchone()
            if row and row["status"] in ("done", "skipped"):
                continue
            try:
                budget.check_or_raise(conn, job_id)
            except BudgetExceeded as e:
                _mark_job_paused(conn, job_id, str(e))
                return
            try:
                _enrich_one_pin(conn, job_id, pin, provider)
                conn.execute(
                    "INSERT OR REPLACE INTO enrichment_job_pins(job_id, pin, status) "
                    "VALUES (?, ?, 'done')",
                    (job_id, pin),
                )
                conn.commit()
            except Exception as e:
                conn.execute(
                    "INSERT OR REPLACE INTO enrichment_job_pins"
                    "(job_id, pin, status, error_message) VALUES (?, ?, 'error', ?)",
                    (job_id, pin, str(e)),
                )
                conn.commit()
        _mark_job_complete(conn, job_id)
```

`_enrich_one_pin` picks the lookup mode based on `parcels.is_llc`:

```python
def _enrich_one_pin(conn, job_id, pin, provider):
    parcel = _load_parcel(conn, pin)
    if _has_fresh_contacts(conn, pin):
        # Per Q3: never re-enrich automatically.
        _record_skip(conn, job_id, pin, reason='already_has_contacts')
        return
    if parcel["is_llc"]:
        # Advanced mode — address-only lookup. Tracerfy returns the humans
        # at the address regardless of LLC name; one API call, $0.10.
        result = provider.lookup(mail_address=parcel["mail_address"])
        lookup_type = "skip_trace_advanced"
    else:
        first, last = _split_owner_name(parcel["owner_name"])
        result = provider.lookup(
            mail_address=parcel["mail_address"],
            owner_first_name=first, owner_last_name=last,
        )
        lookup_type = "skip_trace_normal"
    _save_enrichment_result(
        conn, pin, job_id, result, lookup_type=lookup_type,
        query_name=parcel["owner_name"],
        query_mail_address=parcel["mail_address"],
    )
    # The Tracerfy adapter sets `related_person_name` on each surfaced
    # contact (via source_label or a parallel field) so the UI can render
    # "via Jane Doe". The orchestrator just persists the rows.
    _persist_contacts(conn, pin, result)
```

**Why advanced mode for LLCs:** live test 2026-05-23 confirmed that Tracerfy's advanced mode at an LLC-owned property's address returns multiple human residents with full contact data, at the same cost as normal mode ($0.10). The returned persons may or may not legally own the LLC, but they're physically associated with the property — which is the population we want to contact about an off-market sale anyway. If first-touch outreach fails to reach the owner, the residents will typically point us at the owner or at the property manager. This sidesteps the need for an LLC-pierce provider (CompanyData) entirely in v1.

### 4. Cadence engine — BCC fanout

The cadence engine today returns one `next_due` touch per parcel. The recipient was always the single `contacts.email`. Two changes:

1. **Per-row dead/wrong-person filter.** `next_due_touches_for_parcel` already takes the contact row(s) for a parcel; it now filters out rows where `dead=1 OR wrong_person=1` before evaluating the `requires=email` check. If *zero* alive emails remain, the email touch is `(no email)` in the sequence timeline — same UX as today's "no email on file" state.

2. **Recipient list, not single recipient.** A new pure function `alive_emails_for_parcel(contacts) -> list[str]` returns every `dead=0 AND wrong_person=0 AND email IS NOT NULL` email. The compose modal and the send route both use this list.

`/api/outreach/send` is amended to accept `to_list: list[str]` (BCC) in addition to the existing `to: str` (To header). The UI defaults `to_list` to all alive emails with checkboxes — user can uncheck before clicking Send. Server validates that every item in `to_list` is an alive contact for the pin (defense against tampering).

The Gmail send call uses `cc=''` and `bcc=','.join(to_list)`, with `to=` set to Hunter's own sender address per [RFC 5322 best practice for BCC-only messages](https://datatracker.ietf.org/doc/html/rfc5322) — most providers reject a message with no `To:` header. (This means owners see "To: hsheyman@gmail.com" in their headers, which is fine — it reads as professional, not bulk.)

### 5. Bounce detection — Gmail API poller

Gmail surfaces hard bounces as messages from `mailer-daemon@googlemail.com` (and a few variants). We poll on a 60-minute launchd cycle, identical to the digest job. The poller:

1. Fetches `from:mailer-daemon newer_than:7d` via Gmail API `users.messages.list`.
2. For each message, downloads the body and extracts `Final-Recipient:` headers (and falls back to the `<` `>` pattern in the human-readable body if `Final-Recipient` is absent).
3. For each extracted address, finds matching `contacts` rows with `email=<addr> AND dead=0` and updates `dead=1, dead_at=now, dead_reason='bounce'`.
4. Stores the highest processed `messageId` in `bounce_poll_state` so subsequent runs are incremental.

Inline run-after-send: the `/api/outreach/send` route fires the poller in a background thread after a successful send so a touch-1 fanout that bounces fast (within seconds) updates the UI before the user composes touch 2 hours/days later. This is a best-effort optimization; the 60-min cycle remains the source of truth.

**Edge case the poller handles:** Gmail's `mailer-daemon` messages frequently include the *original recipient* AND a forwarding chain. We extract only the `Final-Recipient` field per RFC 3464; if multiple are present we mark all matching contacts dead. We do NOT parse soft-bounce variants (`This is an automated reply...` deferrals) — those are too varied across servers to regex reliably and don't predict permanent failure.

### 6. Budget cap

Two limits, both configurable in `config/enrichment.yaml`:

```yaml
budget:
  soft_daily_usd: 5.00      # > this → UI confirmation prompt before next call
  hard_per_run_usd: 2.00    # > this → bulk job auto-pauses with reason='budget'
```

`BudgetCap` is a small class that reads `config/enrichment.yaml` once at app start and checks against `SUM(cost_usd) FROM enrichment_results WHERE created_at >= date('now')` (daily) or `... WHERE job_id = ?` (per-run).

The bulk job loop calls `budget.check_or_raise()` before each pin. The single-parcel "Trace" button calls `budget.check_soft()` before issuing the lookup and returns a 409 with a friendly message if exceeded — UI shows "$X spent today, $Y soft cap; click Confirm to override" with an audit log entry of the override.

### 7. UI changes

**Contact section (detail panel)** — replaces today's single-row email/phone with stacked rows. Each row shows:

```
✉ john@gmail.com               rank 1 · tracerfy              [Dead] [Wrong person]
✉ john.smith@acmellc.com       rank 2 · tracerfy              [Dead] [Wrong person]
☎ (312) 555-0199                rank 1 · tracerfy · Mobile     [Dead] [Wrong person]
☎ (312) 555-0288                rank 2 · tracerfy · Landline   [Dead] [Wrong person]

[+ Trace owner]   [+ Add manually]
```

(Tracerfy doesn't return a confidence percentage — it returns a `rank` integer per phone/email. Rank 1 is the provider's best guess. The UI shows rank as a sort-order hint.)

`Trace owner` triggers `POST /api/enrichment/lookup/<pin>` (single-parcel skip trace). Disabled if budget hard cap exceeded.

`Add manually` opens an inline form to add an email or phone without going through a provider — preserves the existing flow for parcels where Hunter knows the owner from another source.

**List view (parcel list)** — new bulk button above the list:

```
[ ⚡ Bulk trace top 20 ]   ← Top 20 of current filter view, skipping already-enriched
```

Click → confirmation modal showing pin count, estimated cost, current filter summary. Confirm → kicks off a background job, modal closes, a progress bar appears at the top of the list view that polls `GET /api/enrichment/job/<id>` every 2s. Done → toast + page refresh.

**Touch 1 (and every touch) compose modal** — recipient field replaced with checkbox list of alive emails:

```
To (BCC, all checked by default):
  ☑ john@gmail.com              rank 1 · tracerfy
  ☑ john.smith@acmellc.com      rank 2 · tracerfy
  ☐ jsmith@oldcompany.com       rank 3 · tracerfy
```

Server validates that checked addresses are still alive at send time (prevents the case where the user opened the modal, then a bounce poll flipped one address dead in the background).

### 8. LLC detection

Already provided by the existing `parcels.is_llc INTEGER` column (populated by the data pipeline at ingest time). The orchestrator reads it directly — no `is_llc()` helper, no regex, no corporate-service-agent blocklist. The "registered agent in Wilmington" problem doesn't apply because we never look up officers; we look up the property address.

Trusts (`XYZ Trust`, `XYZ Family Trust`, `JOHN SMITH TR`) are NOT covered by `parcels.is_llc`. Two cases:
- If `owner_name` is `"SMITH FAMILY TRUST"`, the `_split_owner_name` helper returns `("Smith", "Family Trust")` — Tracerfy normal mode will likely return `hit: false`. We accept the wasted miss-call (cost: $0 since 0 credits on miss) and the parcel ends up with no contacts. User can manually click Trace owner again with a Trust-aware override (deferred to v2 — see "Out of scope").
- If `owner_name` is `"JOHN SMITH TR"` (common assessor encoding for trustee), `_split_owner_name` returns `("John", "Smith Tr")`. Tracerfy will treat "Smith Tr" as a last-name — may or may not hit. Acceptable v1 behavior.

Dedicated trust handling deferred. Most multifamily trusts in Chicago use the LLC structure anyway, so this is a small-population case.

### 9. Per-Q3 decision: no auto re-enrichment

The bulk job's `_has_fresh_contacts` predicate returns `True` if ANY contacts row exists for the pin — there is no time-based decay. Re-enrichment requires a manual flow we are NOT building in v1:

- If Hunter wants to re-enrich a specific parcel, he deletes the existing `contacts` rows in SQLite directly (`DELETE FROM contacts WHERE pin=?`) and clicks Trace owner.

If this becomes painful after 6 months of use, we'll add a "Re-enrich" button then. Until then, the cost-discipline is the right trade.

---

## What's not in scope (v1)

- **Re-enrichment UI / decay flag.** Per Q3, never.
- **Trust pierce.** Surfaced as "manual research needed" only.
- **Multi-level LLC pierce.** One level deep, stop on nested LLC.
- **Soft-bounce parsing.** Too varied to regex reliably; we only flip on hard bounces.
- **Per-recipient open/click tracking.** Would require an SMTP relay (SendGrid etc) — defeats the personal-Gmail-feel of the cadence.
- **Bounce-rate dashboards.** We log dead flips; aggregate reporting is YAGNI until volume justifies it.
- **Auto-replacement of bounced address from an alternate provider.** If Tracerfy's `john@gmail.com` bounces, we mark dead and move on — we do NOT fall back to a second provider hoping for a better hit.

---

## Test strategy

- **Pure-function tests:** `alive_emails_for_parcel`, `_split_owner_name`, `parse_mail_address`, bounce-body parser, budget arithmetic. Fast, no fixtures.
- **Provider tests with stub adapters:** a `StubEnrichmentProvider` that returns canned `EnrichmentResult` data. Bulk job runner gets exercised end-to-end against the stub.
- **Schema migration tests:** assert all ALTERs idempotent.
- **Route tests:** `/api/enrichment/lookup/<pin>`, `/api/enrichment/bulk`, `/api/enrichment/job/<id>`, modified `/api/outreach/send`, `POST /api/contacts/<id>/dead`, `POST /api/contacts/<id>/wrong-person`. Each with a stub provider injected via app factory.
- **Bounce poller test:** sample mailer-daemon body fixtures from real Gmail bounces (anonymized) committed to `tests/fixtures/bounces/`.

No live-provider tests in CI. Manual smoke against the real Tracerfy API (both modes) is a checklist item in the plan's final task.

---

## Cost expectations

At Hunter's stated volume (~30–60 lookups/month) and an assumed ~20% LLC rate:

| Mode | Cost/hit | Triggered when | Monthly est (30–60 parcels) |
|---|---|---|---|
| Tracerfy normal | $0.10 (5 credits) | `parcels.is_llc=0` and owner_name parses to first+last | $1.50–$3.00 |
| Tracerfy advanced | $0.10 (5 credits) | `parcels.is_llc=1` | $1.50–$3.00 |
| Tracerfy miss | $0.00 (0 credits) | Either mode returns `hit: false` | $0 |

Total monthly: **~$3.00–$6.00** at 30–60 parcels/month assuming an even is_llc split. No second-provider LLC pierce cost.

The hard per-run cap is set to $2.50 so the 20-pin bulk button fits with headroom (20 × $0.10 = $2.00; small buffer prevents a dead-stop at the cap). The soft daily cap stays at $5.00 — about 50 lookups/day, well above realistic single-session use.

**Cost confirmed by live test 2026-05-23:** four real API calls in this session (2 normal mode, 2 advanced mode, all hits) — all charged 5 credits each. One miss charged 0 credits. Total spend on validation: 20 credits ≈ $0.40.

---

## Open questions parked outside this spec

- **Q1 — provider choice.** **RESOLVED 2026-05-23:** Tracerfy single provider, two modes — normal (when `parcels.is_llc=0`, supply first/last names) and advanced (when `parcels.is_llc=1`, address only). Both cost 5 credits ≈ $0.10/hit, confirmed by live API tests. CompanyData / OpenCorporates / other LLC-pierce providers are NOT needed — Tracerfy's address-based advanced mode returns humans associated with LLC-owned properties at the same cost as a normal lookup.
- **Trust pierce.** Deferred to v2. Falls through advanced-mode-by-accident for some trust shapes; explicit handling later.
- **Auto-replacement on bounce.** Deferred indefinitely.
- **Tracerfy bulk CSV workflow.** Deferred. The instant API is 5× more per hit ($0.10 vs $0.02) but matches the synchronous-lookup architecture in this spec exactly. If monthly spend trends above ~$15, revisit by adding a `TracerfyBulkProvider` that submits CSVs and polls.
- **`property_owner: false` for everyone.** In our live tests, even known residents came back as `property_owner: false` — Tracerfy's ownership-match logic seems conservative. We don't filter on this flag in v1 (would drop too many useful contacts). Worth revisiting once we have real outreach response data.
