from datetime import date, timedelta

from fastapi.testclient import TestClient

from directory import repository as repo
from directory.api.app import app
from directory.api.deps import get_engine
from directory.config import get_settings
from directory.db import session_scope
from directory.ingest.fetch import FetchResult
from directory.models import Mosque, Occurrence, Source
from directory.web import routes as web_routes

_TODAY = date.today()
_MONTH_NAMES = {
    1: "Jan", 2: "Feb", 3: "Mar", 4: "Apr", 5: "May", 6: "Jun",
    7: "Jul", 8: "Aug", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Dec",
}


def _date_label(d: date) -> str:
    return f"{d.day} {_MONTH_NAMES[d.month]}"


_DAYS = [_TODAY + timedelta(days=i) for i in range(10)]
ROWS = "".join(
    f"<tr><td>{_date_label(d)}</td>"
    f"<td>05:00</td><td>13:30</td><td>18:30</td><td>21:30</td><td>23:00</td></tr>"
    for d in _DAYS
)
HTML = (
    "<table class='t'><tr><th>Date</th><th>Fajr</th><th>Dhuhr</th><th>Asr</th>"
    f"<th>Maghrib</th><th>Isha</th></tr>{ROWS}</table>"
)
CONFIG = (
    '{"shape":"html_table","grid":{"table_selector":"table.t","date":{"index":0},'
    '"columns":['
    '{"kind":"jamaah","prayer":"fajr","index":1},'
    '{"kind":"jamaah","prayer":"dhuhr","index":2},'
    '{"kind":"jamaah","prayer":"asr","index":3},'
    '{"kind":"jamaah","prayer":"maghrib","index":4},'
    '{"kind":"jamaah","prayer":"isha","index":5}]}}'
)


def _setup(engine, monkeypatch):
    monkeypatch.setenv("DIRECTORY_ADMIN_API_KEY", "k")
    get_settings.cache_clear()
    app.dependency_overrides[get_engine] = lambda: engine

    def _fetch(url, **kwargs):
        return FetchResult(url, 200, HTML, "h", error=None)

    monkeypatch.setattr(web_routes, "fetch", _fetch)


def _teardown():
    app.dependency_overrides.clear()
    get_settings.cache_clear()


def _seed(engine):
    with session_scope(engine) as s:
        s.add(Mosque(id="m", name="Masjid M", lat=51.0, lng=-1.0))
        s.add(Source(id="m", mosque_id="m", url="https://m.example/times", config=CONFIG,
                     triage_status="review", review_reason="constant"))


def test_preview_renders_extracted_rows(engine, monkeypatch):
    _setup(engine, monkeypatch)
    _seed(engine)
    resp = TestClient(app).get("/admin/review/m/preview?key=k")
    assert resp.status_code == 200
    assert "05:00" in resp.text
    _teardown()


def test_approve_activates_and_reports(engine, monkeypatch):
    _setup(engine, monkeypatch)
    _seed(engine)
    resp = TestClient(app).post("/admin/review/m/approve?key=k")
    assert resp.status_code == 200
    assert "authored" in resp.text
    with session_scope(engine) as s:
        assert s.query(Occurrence).count() > 0
        assert repo.get_source(s, "m").triage_status == "authored"
    _teardown()


def test_reject_excludes_and_reports(engine, monkeypatch):
    _setup(engine, monkeypatch)
    _seed(engine)
    resp = TestClient(app).post("/admin/review/m/reject?key=k")
    assert resp.status_code == 200
    assert "excluded" in resp.text
    with session_scope(engine) as s:
        assert repo.get_source(s, "m").triage_status == "excluded"
    _teardown()


def test_fix_with_bad_config_reports_error_not_500(engine, monkeypatch):
    _setup(engine, monkeypatch)
    _seed(engine)
    resp = TestClient(app).post("/admin/review/m/fix?key=k", data={"config_json": "{nope"})
    assert resp.status_code == 200
    assert "error" in resp.text.lower()
    _teardown()
