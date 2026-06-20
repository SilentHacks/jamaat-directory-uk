from unittest.mock import patch

from fastapi.testclient import TestClient

from directory.api.app import app
from directory.api.deps import get_engine
from directory.config import get_settings
from directory.db import session_scope
from directory.ingest.runner import ExtractOutcome
from directory.models import Mosque, Source


def _client(engine):
    get_settings.cache_clear()
    app.dependency_overrides[get_engine] = lambda: engine
    return TestClient(app)


def test_refresh_requires_admin(engine, monkeypatch):
    monkeypatch.setenv("DIRECTORY_ADMIN_API_KEY", "secret")
    client = _client(engine)
    assert client.post("/v1/admin/sources/s1/refresh").status_code == 401


def test_refresh_runs_extract_for_source(engine, monkeypatch):
    monkeypatch.setenv("DIRECTORY_ADMIN_API_KEY", "secret")
    with session_scope(engine) as s:
        s.add(Mosque(id="m1", name="M1", lat=52.0, lng=-1.0))
        s.add(Source(id="s1", mosque_id="m1", url="https://x", config="{}",
                     triage_status="authored"))
    client = _client(engine)
    out = ExtractOutcome("s1", True, 5, "auto_accept", "authored")
    with patch("directory.api.admin.extract_source", return_value=out):
        r = client.post("/v1/admin/sources/s1/refresh", headers={"x-api-key": "secret"})
    assert r.status_code == 200
    assert r.json()["rows_written"] == 5


def test_refresh_unknown_source_404(engine, monkeypatch):
    monkeypatch.setenv("DIRECTORY_ADMIN_API_KEY", "secret")
    client = _client(engine)
    r = client.post("/v1/admin/sources/nope/refresh", headers={"x-api-key": "secret"})
    assert r.status_code == 404
