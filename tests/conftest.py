# tests/conftest.py
import pytest
from pathlib import Path
from pipeline.config import GeographyConfig
from pipeline.db import init_db
from pipeline.socrata import SocrataClient


@pytest.fixture
def db_path(tmp_path):
    p = tmp_path / "test.db"
    init_db(p)
    return p


@pytest.fixture
def geo():
    return GeographyConfig(
        name="Test",
        polygon=[(41.95, -87.69), (41.95, -87.62),
                 (41.92, -87.62), (41.92, -87.69)],
        bbox=(41.92, 41.95, -87.69, -87.62),
    )


@pytest.fixture
def cook_client():
    return SocrataClient(domain="datacatalog.cookcountyil.gov",
                         app_token="TEST_TOKEN", retry_backoff=0.0)


@pytest.fixture
def cdp_client():
    return SocrataClient(domain="data.cityofchicago.org",
                         app_token="TEST_TOKEN", retry_backoff=0.0)


FIXTURES = Path(__file__).parent / "fixtures"
