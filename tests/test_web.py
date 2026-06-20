import pytest
from fastapi.testclient import TestClient

from directory.api.app import create_app
from directory.api.deps import get_engine


@pytest.fixture
def client(seeded):
    app = create_app()
    app.dependency_overrides[get_engine] = lambda: seeded
    return TestClient(app)


def test_index_lists_mosques(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Leicester Masjid" in r.text
    assert "London Masjid" in r.text


def test_search_partial_filters(client):
    r = client.get("/search", params={"q": "london"})
    assert r.status_code == 200
    assert "London Masjid" in r.text
    assert "Leicester Masjid" not in r.text


def test_detail_page(client):
    r = client.get("/mosque/lon")
    assert r.status_code == 200
    assert "London Masjid" in r.text
    assert "not recorded" in r.text  # null website


def test_detail_404(client):
    assert client.get("/mosque/nope").status_code == 404
