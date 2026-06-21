import pytest
from fastapi.testclient import TestClient

from directory.api.app import create_app
from directory.api.deps import get_engine


@pytest.fixture
def client(seeded):
    app = create_app()
    app.dependency_overrides[get_engine] = lambda: seeded
    return TestClient(app)


def test_list_all(client):
    r = client.get("/v1/mosques")
    assert r.status_code == 200
    ids = {m["id"] for m in r.json()}
    assert ids == {"leic", "lon"}


def test_list_filter_city(client):
    r = client.get("/v1/mosques", params={"city": "London"})
    assert [m["id"] for m in r.json()] == ["lon"]


def test_has_times_flag(client):
    r = client.get("/v1/mosques", params={"has_times": "true"})
    body = r.json()
    assert [m["id"] for m in body] == ["leic"]
    assert body[0]["has_times"] is True


def test_near_requires_radius(client):
    r = client.get("/v1/mosques", params={"near": "51.5,-0.1"})
    assert r.status_code == 422


def test_detail_and_404(client):
    assert client.get("/v1/mosques/leic").json()["name"] == "Leicester Masjid"
    assert client.get("/v1/mosques/nope").status_code == 404


def test_malformed_bbox_returns_422(client):
    r = client.get("/v1/mosques", params={"bbox": "0,0,abc,1"})
    assert r.status_code == 422


def test_malformed_near_returns_422(client):
    r = client.get("/v1/mosques", params={"near": "abc,1", "radius_km": 5})
    assert r.status_code == 422


def test_detail_exposes_source_status_and_jumuah_missing(seeded):
    from directory.db import session_scope
    from directory.models import Source

    with session_scope(seeded) as s:
        s.add(Source(id="leic", mosque_id="leic", url="https://a.example", config="{}",
                     triage_status="authored", flags='["jumuah_missing"]'))
    app = create_app()
    app.dependency_overrides[get_engine] = lambda: seeded
    client = TestClient(app)
    body = client.get("/v1/mosques/leic").json()
    assert body["source_status"] == "authored"
    assert body["jumuah_missing"] is True
    # the mosque with no source reports nulls/false
    other = client.get("/v1/mosques/lon").json()
    assert other["source_status"] is None
    assert other["jumuah_missing"] is False
