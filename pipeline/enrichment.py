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
    """Returns phones from contact rows where dead=0 and wrong_person=0."""
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
        default_city: str = "",
        default_state: str = "",
        default_zip: str = "",
    ) -> EnrichmentResult:
        """Returns surfaced contacts for a parcel.

        When owner_first_name AND owner_last_name are supplied (both
        truthy), the provider uses normal-mode by-name lookup. When EITHER
        is empty/None, the provider falls through to advanced-mode
        address-only lookup. This lets the orchestrator pass through
        whatever it has without branching on mode itself.

        default_city / default_state / default_zip fill in pieces the
        provider can't extract from mail_address. The Cook County assessor
        ships street-only strings, so the caller has to supply Chicago/IL
        + the parcel's zip_code or Tracerfy 400s on the city field.
        """
        ...


# ---------- Budget caps ----------

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


# ---------- Bulk orchestrator ----------
#
# The orchestrator is stateless, fully synchronous, and checkpointed per pin.
# Each pin commits independently, so a crash mid-run leaves
# enrichment_job_pins in a defensible state; a resume just re-runs by
# skipping pins whose status is already 'done' or 'skipped'. The Flask
# route in T10 will wrap run_bulk_enrichment() in a background thread.

import json as _json
import sqlite3 as _sqlite3  # noqa: F401  (kept for type-hint clarity in future)
from contextlib import closing


def create_enrichment_job(conn, pin_list: list[str]) -> int:
    """Create a new enrichment_jobs row in 'running' state.

    The full input pin set is serialized into pin_list_json so a future
    resume can reconstruct the original work set independently of whatever
    per-pin progress rows have been written. Returns the new job id.
    """
    cur = conn.execute(
        "INSERT INTO enrichment_jobs(pin_list_json, status) VALUES (?, 'running')",
        (_json.dumps(pin_list),),
    )
    return cur.lastrowid


def _has_fresh_contacts(conn, pin: str) -> bool:
    """Per the spec: any existing contact row counts as fresh; no time-decay.

    A parcel with even one prior contact (manual or enrichment-sourced) is
    treated as already enriched and skipped — avoids re-paying the provider
    for pins that already have something workable. Operators wanting a
    re-trace must explicitly clear the contacts row first.
    """
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM contacts WHERE pin=?", (pin,)
    ).fetchone()
    n = row["c"] if hasattr(row, "keys") else row[0]
    return n > 0


def _save_enrichment_result(conn, *, pin, job_id, lookup_type, query_name,
                            query_mail_address, result) -> None:
    """Persist the provider call to enrichment_results and bump the job's
    running cost total. Stores the raw_response_json verbatim so future
    backfills can extract new fields without re-paying for the lookup."""
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
    'tracerfy:Mobile:rank-1:via=Jane Doe'. Returns None if no via= present.

    The provider encodes the related person inline in the source_label so
    one source of truth survives even if the raw response is purged. We
    extract it once at insert time and stash it in contacts.related_person_name
    so the UI can render 'via Jane Doe' without re-parsing on every render.
    """
    for part in (source_label or "").split(":"):
        if part.startswith("via="):
            name = part[len("via="):].strip()
            return name or None
    return None


def _persist_contacts(conn, pin: str, result: EnrichmentResult) -> None:
    """Insert one contacts row per surfaced email / phone. Dedup by value.

    The per-contact source_label goes into enrichment_source. The person's
    name is extracted from 'via=...' and stored in related_person_name so
    the UI can render 'via Jane Doe' without re-parsing on every render.
    Existing rows with the same (pin, email) or (pin, phone) are left
    untouched so a re-run never doubles up the same hit.
    """
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
                    provider: EnrichmentProvider) -> EnrichmentResult:
    """Run one provider lookup for one pin and persist the results.

    Picks the lookup mode based on parcels.is_llc:
      - is_llc=0 → split owner_name into first/last and call normal mode
      - is_llc=1 → omit names, call advanced mode (address-only)

    Returns the EnrichmentResult so the single-pin endpoint can surface
    provider errors (status='error') as HTTP 502 instead of silently
    succeeding with an empty contacts list. The bulk path discards the
    return value and relies on its own per-pin checkpoint table.

    Raises ValueError if the pin is missing from parcels; the caller is
    expected to catch and write the per-pin error row.
    """
    parcel = conn.execute(
        "SELECT * FROM parcels WHERE pin=?", (pin,)
    ).fetchone()
    if parcel is None:
        raise ValueError(f"pin {pin} not in parcels table")
    mail = parcel["mail_address"]
    # Cook County mail_address strings are usually street-only; the parser
    # can't recover city/state/zip from them. Supply Chicago defaults plus
    # the parcel's own zip_code so the provider has something to send.
    defaults = dict(
        default_city="Chicago",
        default_state="IL",
        default_zip=parcel["zip_code"] or "",
    )
    if parcel["is_llc"]:
        result = provider.lookup(mail_address=mail, **defaults)
        lookup_type = "skip_trace_advanced"
    else:
        first, last = split_owner_name(parcel["owner_name"] or "")
        result = provider.lookup(
            mail_address=mail,
            owner_first_name=first, owner_last_name=last,
            **defaults,
        )
        lookup_type = "skip_trace_normal"
    _save_enrichment_result(
        conn, pin=pin, job_id=job_id, lookup_type=lookup_type,
        query_name=parcel["owner_name"], query_mail_address=mail,
        result=result,
    )
    _persist_contacts(conn, pin, result)
    return result


def run_bulk_enrichment(
    *,
    conn_factory,
    job_id: int,
    pin_list: list[str],
    provider: EnrichmentProvider,
    budget: BudgetCap,
) -> None:
    """Drive a batch enrichment job to completion (or pause).

    For each pin in order:
      1. Skip if already checkpointed as 'done' or 'skipped' (resume case).
      2. Skip (and checkpoint as 'skipped') if the parcel already has any
         contact row — avoids re-paying for known-enriched pins.
      3. Check the per-run budget cap; if it would be exceeded, mark the
         job 'paused' with the reason and return immediately.
      4. Run the provider lookup, persist results + contacts, checkpoint
         the pin as 'done'.
      5. On any other exception, checkpoint the pin as 'error' with the
         message and keep going.

    Commits between pins so a crash leaves a usable checkpoint. The final
    UPDATE flips the job to 'complete' with a project-standard ISO-Z
    completed_at timestamp (matches enrichment_jobs.created_at convention).
    """
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
                # Prefix with "budget:" so operators (and the test suite)
                # can pattern-match the pause reason without parsing the
                # full BudgetCap error message.
                conn.execute(
                    "UPDATE enrichment_jobs SET status='paused', paused_reason=? "
                    "WHERE id=?", (f"budget: {e}", job_id),
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
            "completed_at=strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE id=?",
            (job_id,),
        )
        conn.commit()
