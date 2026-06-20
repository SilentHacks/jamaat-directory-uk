import pytest
from fastapi.testclient import TestClient

from directory.api.app import create_app
from directory.api.deps import get_engine
from directory.config import Settings, get_settings


@pytest.fixture
def client(seeded):
    app = create_app()
    app.dependency_overrides[get_engine] = lambda: seeded
    return TestClient(app), app


def test_health_public(client):
    c, _ = client
    r = c.get("/v1/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "mosques": 2}


def test_admin_503_when_unconfigured(client):
    c, app = client
    app.dependency_overrides[get_settings] = lambda: Settings(admin_api_key=None)
    # require_admin calls get_settings() directly, so patch via cache clear:
    get_settings.cache_clear()
    r = c.get("/v1/admin/sources")
    assert r.status_code == 503


def test_admin_requires_key(client, monkeypatch):
    c, _ = client
    monkeypatch.setenv("DIRECTORY_ADMIN_API_KEY", "secret")
    get_settings.cache_clear()
    assert c.get("/v1/admin/sources").status_code == 401
    ok = c.get("/v1/admin/sources", headers={"X-API-Key": "secret"})
    assert ok.status_code == 200
    assert ok.json() == []
    get_settings.cache_clear()
