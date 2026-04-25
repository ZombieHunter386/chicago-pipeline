import pytest
from webapp.app import create_app


@pytest.fixture
def client(db_path):
    app = create_app(db_path=db_path, feature_outreach=False)
    app.testing = True
    return app.test_client()


def test_index_returns_200_and_html(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.mimetype == "text/html"
    assert b"Chicago Multifamily Pipeline" in resp.data
