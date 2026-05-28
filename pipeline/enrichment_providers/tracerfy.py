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
mode is used.
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
    # No commas: if we successfully stripped a state+zip, the last
    # whitespace-separated token is the city (e.g. "123 Main St Chicago").
    # Otherwise (e.g. bare "123 Main St"), the whole string is the address.
    if state and zip_code:
        tokens = s.split()
        if len(tokens) >= 2:
            return {"address": " ".join(tokens[:-1]), "city": tokens[-1],
                    "state": state, "zip": zip_code}
    return {"address": s.strip(), "city": "", "state": state, "zip": zip_code}


@dataclass
class TracerfyProvider:
    api_key: str
    name: str = "tracerfy"
    cost_per_lookup_usd: float = 0.10  # 5 credits × $0.02/credit

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
        # Mode selection: if EITHER name is blank/None, fall through to
        # advanced (find_owner=true) — Tracerfy normal mode requires both.
        use_advanced = not (first and last)
        body = {
            "address": parsed["address"],
            "city": parsed["city"],
            "state": parsed["state"] or "IL",
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
                cost_usd=0.0,
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
