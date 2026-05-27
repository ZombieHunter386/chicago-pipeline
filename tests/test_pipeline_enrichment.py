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
