from fastapi.testclient import TestClient

from directory.api import admin
from directory.api.app import app
from directory.api.deps import get_engine
from directory.config import get_settings
from directory.db import session_scope
from directory.ingest.discover import DiscoverOutcome
from directory.models import Mosque


def _setup(engine, monkeypatch):
    monkeypatch.setenv("DIRECTORY_ADMIN_API_KEY", "k")
    get_settings.cache_clear()
    app.dependency_overrides[get_engine] = lambda: engine
    # Stub the actual funnel so the endpoint test needs no network.
    monkeypatch.setattr(
        admin,
        "discover_mosque",
        lambda engine, mosque_id, *, candidate_root: DiscoverOutcome(
            mosque_id, "candidate", None, detail="stub"
        ),
    )


def _teardown():
    app.dependency_overrides.clear()
    get_settings.cache_clear()


def test_discover_endpoint_requires_key(engine, monkeypatch):
    _setup(engine, monkeypatch)
    with session_scope(engine) as s:
        s.add(Mosque(id="m", name="m", lat=51.0, lng=-1.0, website_url="https://m.example/"))
    client = TestClient(app)
    assert client.post("/v1/admin/mosques/m/discover").status_code == 401
    _teardown()


def test_discover_endpoint_runs_and_404s(engine, monkeypatch):
    _setup(engine, monkeypatch)
    with session_scope(engine) as s:
        s.add(Mosque(id="m", name="m", lat=51.0, lng=-1.0, website_url="https://m.example/"))
    client = TestClient(app)

    resp = client.post("/v1/admin/mosques/m/discover", headers={"x-api-key": "k"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["mosque_id"] == "m"
    assert body["outcome"] == "candidate"

    assert client.post(
        "/v1/admin/mosques/missing/discover", headers={"x-api-key": "k"}
    ).status_code == 404
    _teardown()
