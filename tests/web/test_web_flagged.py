from fastapi.testclient import TestClient

from directory.api.app import app
from directory.api.deps import get_engine
from directory.config import get_settings
from directory.db import session_scope
from directory.models import Mosque, Source


def _setup(engine, monkeypatch):
    monkeypatch.setenv("DIRECTORY_ADMIN_API_KEY", "k")
    get_settings.cache_clear()
    app.dependency_overrides[get_engine] = lambda: engine


def _teardown():
    app.dependency_overrides.clear()
    get_settings.cache_clear()


def test_flagged_list_requires_key(engine, monkeypatch):
    _setup(engine, monkeypatch)
    assert TestClient(app).get("/admin/flagged").status_code == 401
    _teardown()


def test_flagged_list_shows_jumuah_missing_sources(engine, monkeypatch):
    _setup(engine, monkeypatch)
    with session_scope(engine) as s:
        s.add(Mosque(id="m", name="Masjid Flagged", city="London", lat=51.0, lng=-1.0))
        s.add(Source(id="m", mosque_id="m", url="https://m.example", config="{}",
                     triage_status="authored", flags='["jumuah_missing"]'))
        s.add(Mosque(id="c", name="Masjid Clean", city="London", lat=51.0, lng=-1.0))
        s.add(Source(id="c", mosque_id="c", url="https://c.example", config="{}",
                     triage_status="authored"))
    resp = TestClient(app).get("/admin/flagged?key=k")
    assert resp.status_code == 200
    assert "Masjid Flagged" in resp.text
    assert "Masjid Clean" not in resp.text  # not flagged → not blocking
    _teardown()


def test_partial_review_detail_shows_missing_reason(engine, monkeypatch):
    _setup(engine, monkeypatch)
    with session_scope(engine) as s:
        s.add(Mosque(id="p", name="Masjid Partial", city="London", lat=51.0, lng=-1.0))
        s.add(Source(id="p", mosque_id="p", url="https://p.example", config="{}",
                     triage_status="review",
                     review_reason="incomplete: missing ['isha']"))
    resp = TestClient(app).get("/admin/review/p?key=k")
    assert resp.status_code == 200
    assert "incomplete: missing" in resp.text
    assert "isha" in resp.text
    _teardown()
