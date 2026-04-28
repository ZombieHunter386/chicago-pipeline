"""Shared address normalization and matching helpers.

Used by sources/assessor_addresses.py for owner/property absentee detection,
and by sources/cdp_* fetchers that match address-only records (permits,
violations, scofflaw, vacant-building violations) to PINs.

Three things live here:
  * `street_key(addr)` — canonical "<num> <dir> <street...> <suffix>" form.
  * `expand_address_range(addr)` — handle "100-104 W DIVERSEY" by expanding
     to even-only or odd-only members so the matcher can hit any of them.
  * `address_distance(a, b)` — token-Levenshtein on the canonical form,
     used as the "fuzzy" tier of the matcher. Same number+direction is
     required up-front; this only forgives spelling differences in the
     street-name tokens.
  * `is_llc(name)` — string-only LLC detection, kept here so the matcher
     can stay decoupled from the source modules.
"""
from __future__ import annotations
import re


# ----------------------------- Constants ---------------------------------

LLC_PATTERN = re.compile(
    r"\b(LLC|L\.L\.C|CORP|CORPORATION|INC|INCORPORATED|TRUST|LP|L\.P|PARTNERS|PARTNERSHIP|LLP|L\.L\.P|HOLDINGS|REALTY|PROPERTIES)\b\.?",
    re.IGNORECASE,
)

SUFFIX_MAP = {
    "AVENUE": "AVE", "AV": "AVE", "AVE": "AVE",
    "BOULEVARD": "BLVD", "BL": "BLVD", "BLVD": "BLVD",
    "PARKWAY": "PKWY", "PKY": "PKWY", "PKWY": "PKWY",
    "STREET": "ST", "ST": "ST",
    "ROAD": "RD", "RD": "RD",
    "DRIVE": "DR", "DR": "DR",
    "LANE": "LN", "LN": "LN",
    "COURT": "CT", "CT": "CT",
    "PLACE": "PL", "PL": "PL",
    "TERRACE": "TER", "TER": "TER",
    "HIGHWAY": "HWY", "HWY": "HWY",
    "PLAZA": "PLZ", "PLZ": "PLZ",
    "SQUARE": "SQ", "SQ": "SQ",
    "WAY": "WAY",
}
DIRECTION_MAP = {
    "NORTH": "N", "N": "N",
    "SOUTH": "S", "S": "S",
    "EAST": "E", "E": "E",
    "WEST": "W", "W": "W",
    "NORTHEAST": "NE", "NE": "NE",
    "NORTHWEST": "NW", "NW": "NW",
    "SOUTHEAST": "SE", "SE": "SE",
    "SOUTHWEST": "SW", "SW": "SW",
}
SUFFIX_TOKENS = set(SUFFIX_MAP.values())
DIRECTION_TOKENS = set(DIRECTION_MAP.values())
UNIT_MARKER_RE = re.compile(r"\b(UNIT|APT|APARTMENT|STE|SUITE|FL|FLOOR)\s+\S+", re.IGNORECASE)
HASH_UNIT_RE = re.compile(r"#\s*\S+")


# ----------------------------- Public API --------------------------------

def is_llc(name: str | None) -> bool:
    if not name:
        return False
    return bool(LLC_PATTERN.search(name))


def street_key(addr: str | None) -> str | None:
    """Canonicalize an address to '<number> <dir> <street...> <suffix>'.
    Drops unit numbers (UNIT 5, APT 3, #4, FL 2, trailing 1E/2W condo tokens).
    Returns None for empty input.
    """
    if not addr:
        return None
    s = re.sub(r"\s+", " ", addr).strip().upper()
    s = UNIT_MARKER_RE.sub(" ", s)
    s = HASH_UNIT_RE.sub(" ", s)
    s = re.sub(r"([A-Z])(\d)", r"\1 \2", s)
    s = re.sub(r"(\d)([A-Z])", r"\1 \2", s)
    s = re.sub(r"[.,]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    if not s:
        return None
    tokens = [DIRECTION_MAP.get(t, SUFFIX_MAP.get(t, t)) for t in s.split()]
    last_suffix = -1
    for i, t in enumerate(tokens):
        if t in SUFFIX_TOKENS:
            last_suffix = i
    if last_suffix >= 0:
        tokens = tokens[:last_suffix + 1]
    return " ".join(tokens) or None


def is_absentee(prop_addr: str | None, mail_addr: str | None) -> bool:
    p = street_key(prop_addr)
    m = street_key(mail_addr)
    if p is None or m is None:
        return False
    return p != m


_RANGE_NUM_RE = re.compile(r"^(\d+)[-–](\d+)$")


def split_canonical(key: str) -> tuple[str | None, str | None, str | None, tuple[str, ...]]:
    """Pull (number, direction, suffix, middle_tokens) out of a canonical key.
    `middle_tokens` is everything between direction and the trailing suffix
    — this is what gets compared in the fuzzy tier.

    Examples:
        "100 W DIVERSEY PKWY" -> ("100", "W", "PKWY", ("DIVERSEY",))
        "100 W DIVERSEY"      -> ("100", "W", None, ("DIVERSEY",))
        "100-104 W DIVERSEY"  -> ("100-104", "W", None, ("DIVERSEY",))
        "DIVERSEY PKWY"       -> (None, None, "PKWY", ("DIVERSEY",))
    """
    if not key:
        return None, None, None, ()
    tokens = key.split()
    number, direction, suffix = None, None, None
    if tokens and (tokens[0].isdigit() or _RANGE_NUM_RE.match(tokens[0])):
        number = tokens[0]
        tokens = tokens[1:]
    if tokens and tokens[0] in DIRECTION_TOKENS:
        direction = tokens[0]
        tokens = tokens[1:]
    if tokens and tokens[-1] in SUFFIX_TOKENS:
        suffix = tokens[-1]
        tokens = tokens[:-1]
    return number, direction, suffix, tuple(tokens)


def expand_address_range(key: str | None) -> list[str]:
    """Given a canonical key whose number is "100-104", return one canonical
    string per number in the range matching its parity. So "100-104 W DIVERSEY"
    expands to ["100 W DIVERSEY", "102 W DIVERSEY", "104 W DIVERSEY"]; the
    first number's parity is taken as the canonical parity (even/odd are
    typically not mixed on a single Chicago block).

    Returns the input unchanged if the number isn't a range.
    """
    if not key:
        return []
    number, direction, suffix, middle = split_canonical(key)
    if not number:
        return [key]
    m = _RANGE_NUM_RE.match(number)
    if not m:
        return [key]
    lo, hi = int(m.group(1)), int(m.group(2))
    if hi < lo:
        lo, hi = hi, lo
    parity = lo % 2
    out = []
    for n in range(lo, hi + 1):
        if n % 2 != parity:
            continue
        parts = [str(n)]
        if direction:
            parts.append(direction)
        parts.extend(middle)
        if suffix:
            parts.append(suffix)
        out.append(" ".join(parts))
    return out or [key]


def levenshtein(a: str, b: str) -> int:
    """Classic O(len(a)*len(b)) Levenshtein. Bounded inputs (street tokens),
    no need for an optimized variant."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(
                cur[j - 1] + 1,
                prev[j] + 1,
                prev[j - 1] + (ca != cb),
            ))
        prev = cur
    return prev[-1]


def fuzzy_distance(a: str | None, b: str | None) -> int:
    """Compare two canonical street keys at the *street-name* level only.
    Same number + same direction is required (caller enforces); this returns
    Levenshtein on the joined middle tokens. Returns a large number when
    either side is empty.
    """
    if not a or not b:
        return 9_999
    _, _, _, ma = split_canonical(a)
    _, _, _, mb = split_canonical(b)
    return levenshtein(" ".join(ma), " ".join(mb))
