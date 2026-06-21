from fastapi.testclient import TestClient

from directory.api import admin
from directory.api.app import app
from directory.api.deps import get_engine
from directory.config import get_settings
from directory.db import session_scope
from directory.ingest.author import AuthorOutcome
from directory.models import Mosque, Source


def _setup(engine, monkeypatch):
    monkeypatch.setenv("DIRECTORY_ADMIN_API_KEY", "k")
    get_settings.cache_clear()
    app.dependency_overrides[get_engine] = lambda: engine
    monkeypatch.setattr(
        admin, "author_mosque",
        lambda engine, mosque_id, **kwargs: AuthorOutcome(mosque_id, "authored", "cheap"),
    )


def _teardown():
    app.dependency_overrides.clear()
    get_settings.cache_clear()


def test_author_endpoint_requires_key(engine, monkeypatch):
    _setup(engine, monkeypatch)
    with session_scope(engine) as s:
        s.add(Mosque(id="m", name="m", lat=51.0, lng=-1.0, website_url="https://m.example/"))
    assert TestClient(app).post("/v1/admin/mosques/m/author").status_code == 401
    _teardown()


def test_author_endpoint_runs_and_404s(engine, monkeypatch):
    _setup(engine, monkeypatch)
    with session_scope(engine) as s:
        s.add(Mosque(id="m", name="m", lat=51.0, lng=-1.0, website_url="https://m.example/"))
    client = TestClient(app)
    resp = client.post("/v1/admin/mosques/m/author", headers={"x-api-key": "k"})
    assert resp.status_code == 200
    assert resp.json()["outcome"] == "authored"
    assert client.post(
        "/v1/admin/mosques/missing/author", headers={"x-api-key": "k"}
    ).status_code == 404
    _teardown()


def test_review_list_returns_review_rows(engine, monkeypatch):
    _setup(engine, monkeypatch)
    with session_scope(engine) as s:
        s.add(Mosque(id="m", name="Masjid M", lat=51.0, lng=-1.0))
        s.add(Source(id="m", mosque_id="m", url="https://m.example", triage_status="review",
                     review_reason="constant", confidence=0.7))
    resp = TestClient(app).get("/v1/admin/review", headers={"x-api-key": "k"})
    assert resp.status_code == 200
    body = resp.json()
    assert body == [
        {"source_id": "m", "mosque_id": "m", "name": "Masjid M",
         "url": "https://m.example", "reason": "constant", "confidence": 0.7}
    ]
    _teardown()
