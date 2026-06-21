# tests/test_web_review.py
from fastapi.testclient import TestClient

from directory.api.app import app
from directory.api.deps import get_engine
from directory.config import get_settings
from directory.db import session_scope
from directory.models import Mosque, Source

CONFIG = '{"shape":"rules","rules":{"rules":[{"prayer":"fajr","fixed":"05:00"}]}}'


def _setup(engine, monkeypatch):
    monkeypatch.setenv("DIRECTORY_ADMIN_API_KEY", "k")
    get_settings.cache_clear()
    app.dependency_overrides[get_engine] = lambda: engine


def _teardown():
    app.dependency_overrides.clear()
    get_settings.cache_clear()


def _seed(engine):
    with session_scope(engine) as s:
        s.add(Mosque(id="m", name="Masjid M", city="London", lat=51.0, lng=-1.0,
                     website_url="https://m.example/"))
        s.add(Source(id="m", mosque_id="m", url="https://m.example/times", config=CONFIG,
                     triage_status="review", review_reason="single column"))


def test_review_list_requires_key(engine, monkeypatch):
    _setup(engine, monkeypatch)
    assert TestClient(app).get("/admin/review").status_code == 401
    _teardown()


def test_review_list_renders_items_with_key(engine, monkeypatch):
    _setup(engine, monkeypatch)
    _seed(engine)
    resp = TestClient(app).get("/admin/review?key=k")
    assert resp.status_code == 200
    assert "Masjid M" in resp.text
    assert "single column" in resp.text
    assert "/admin/review/m?key=k" in resp.text
    _teardown()


def test_review_detail_renders_config_and_forms(engine, monkeypatch):
    _setup(engine, monkeypatch)
    _seed(engine)
    resp = TestClient(app).get("/admin/review/m", headers={"x-api-key": "k"})
    assert resp.status_code == 200
    assert "https://m.example/times" in resp.text
    assert "rules" in resp.text                # config rendered
    assert "/admin/review/m/approve" in resp.text
    assert "/admin/review/m/reject" in resp.text
    assert "/admin/review/m/fix" in resp.text
    _teardown()


def test_review_detail_404(engine, monkeypatch):
    _setup(engine, monkeypatch)
    assert TestClient(app).get("/admin/review/missing?key=k").status_code == 404
    _teardown()
