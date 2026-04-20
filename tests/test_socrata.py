# tests/test_socrata.py
import responses
import pytest
from pipeline.socrata import SocrataClient, SocrataError


@responses.activate
def test_fetch_paginated_single_page():
    url = "https://datacatalog.cookcountyil.gov/resource/abc-123.json"
    responses.add(
        responses.GET, url,
        json=[{"pin": "1"}, {"pin": "2"}], status=200,
    )
    client = SocrataClient(domain="datacatalog.cookcountyil.gov", app_token="TKN")
    rows = list(client.fetch("abc-123", limit=50000))
    assert len(rows) == 2
    # Ensure app token header was sent
    assert responses.calls[0].request.headers.get("X-App-Token") == "TKN"


@responses.activate
def test_fetch_paginated_multiple_pages():
    url = "https://data.cityofchicago.org/resource/xyz-456.json"
    page1 = [{"id": str(i)} for i in range(50000)]
    page2 = [{"id": str(i)} for i in range(50000, 50010)]
    responses.add(responses.GET, url, json=page1, status=200)
    responses.add(responses.GET, url, json=page2, status=200)
    responses.add(responses.GET, url, json=[], status=200)
    client = SocrataClient(domain="data.cityofchicago.org", app_token="TKN")
    rows = list(client.fetch("xyz-456", limit=50000))
    assert len(rows) == 50010


@responses.activate
def test_fetch_with_where_clause():
    url = "https://data.cityofchicago.org/resource/xyz-456.json"
    responses.add(responses.GET, url, json=[{"id": "1"}], status=200)
    client = SocrataClient(domain="data.cityofchicago.org", app_token="TKN")
    list(client.fetch("xyz-456", where="lat between 40 and 41", limit=50000))
    qs = responses.calls[0].request.url
    assert "%24where=" in qs or "$where=" in qs


@responses.activate
def test_fetch_retries_on_500_then_succeeds():
    url = "https://data.cityofchicago.org/resource/xyz-456.json"
    responses.add(responses.GET, url, status=500)
    responses.add(responses.GET, url, json=[{"id": "1"}], status=200)
    responses.add(responses.GET, url, json=[], status=200)
    client = SocrataClient(domain="data.cityofchicago.org", app_token="TKN",
                           retry_backoff=0.0)
    rows = list(client.fetch("xyz-456", limit=50000))
    assert len(rows) == 1


@responses.activate
def test_fetch_does_not_retry_on_4xx():
    url = "https://data.cityofchicago.org/resource/xyz-456.json"
    responses.add(responses.GET, url, status=404, body="not found")
    client = SocrataClient(domain="data.cityofchicago.org", app_token="TKN",
                           retry_backoff=0.0, max_retries=5)
    with pytest.raises(SocrataError):
        list(client.fetch("xyz-456", limit=50000))
    # Only one call should have been made (no retries on 4xx).
    assert len(responses.calls) == 1


@responses.activate
def test_fetch_raises_after_max_retries():
    url = "https://data.cityofchicago.org/resource/xyz-456.json"
    for _ in range(5):
        responses.add(responses.GET, url, status=500)
    client = SocrataClient(domain="data.cityofchicago.org", app_token="TKN",
                           retry_backoff=0.0, max_retries=3)
    with pytest.raises(SocrataError):
        list(client.fetch("xyz-456", limit=50000))
