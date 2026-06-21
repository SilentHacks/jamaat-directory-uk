import pytest
from fastapi.testclient import TestClient

from directory.api.app import create_app
from directory.api.deps import get_engine


@pytest.fixture
def client(seeded):
    app = create_app()
    app.dependency_overrides[get_engine] = lambda: seeded
    return TestClient(app)


def test_mosque_times_range(client):
    r = client.get("/v1/mosques/leic/times", params={"date": "2026-06-21"})
    assert r.status_code == 200
    day = r.json()[0]
    assert day["fajr"] == "05:00"
    assert day["begin"] == {"fajr": "04:45"}
    assert [s["time"] for s in day["jumuah"]] == ["13:00", "13:45"]


def test_mosque_times_404(client):
    assert client.get("/v1/mosques/nope/times").status_code == 404


def test_times_workhorse_by_prayer(client):
    r = client.get("/v1/times", params={"date": "2026-06-21", "prayer": "fajr"})
    body = r.json()
    assert len(body) == 1
    assert body[0]["mosque_id"] == "leic"
    assert body[0]["jamaah_time"] == "05:00"


def test_snapshot_has_etag_and_counts(client):
    r = client.get("/v1/snapshot")
    assert r.status_code == 200
    etag = r.headers.get("ETag")
    assert etag is not None
    assert etag.startswith('"') and etag.endswith('"')
    assert r.json()["count"] == 2
