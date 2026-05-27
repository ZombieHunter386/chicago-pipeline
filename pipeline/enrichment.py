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
    ) -> EnrichmentResult:
        """Returns surfaced contacts for a parcel.

        When owner_first_name AND owner_last_name are supplied (both
        truthy), the provider uses normal-mode by-name lookup. When EITHER
        is empty/None, the provider falls through to advanced-mode
        address-only lookup. This lets the orchestrator pass through
        whatever it has without branching on mode itself.
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
