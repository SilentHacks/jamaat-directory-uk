from datetime import date
from pathlib import Path

import httpx

from directory import repository as repo
from directory.db import session_scope
from directory.ingest.discover import discover_mosque
from directory.ingest.fetch import FetchResult
from directory.models import Mosque

FIXTURES = Path(__file__).parent.parent / "fixtures"
JUNE = (FIXTURES / "dom_records_blackhall.html").read_text()
JULY = (FIXTURES / "dom_records_blackhall_jul.html").read_text()

HOME = (
    '<html><body><nav>'
    '<a href="/monthly-timetable">Prayer Times</a>'
    "</nav><div>Welcome to the mosque.</div></body></html>"
)
# The JS shell served statically: prayer names but no times, so discovery renders.
SHELL = (
    "<html><body><h1>Monthly prayer times</h1>"
    "<div>Fajr</div><div>Dhuhr</div><div>Asr</div><div>Maghrib</div><div>Isha</div>"
    "<div>Loading prayer times…</div></body></html>"
)
SUBPAGE = "https://m.example/monthly-timetable"


def _fetcher(url, *, requires_js=False, etag=None, last_modified=None, client=None,
             renderer=None, timeout=20.0):
    if "monthly-timetable" in url:
        if requires_js:  # headless render of the current month
            return FetchResult(url, 200, JUNE, "h")
        return FetchResult(url, 200, SHELL, "h")
    if url.rstrip("/") == "https://m.example":
        return FetchResult(url, 200, HOME, "h")
    return FetchResult(url, 404, "", None)  # unknown ranked sub-paths


def _nav_renderer(url, nav, months):
    # One HTML per month in the horizon (current first), driven by the forward nav.
    by_month = {(2026, 6): JUNE, (2026, 7): JULY}
    return [by_month.get(m, "") for m in months]


def test_blackhall_style_record_stream_discovers_and_pages(engine, tmp_path):
    with session_scope(engine) as s:
        s.add(Mosque(id="bh", name="Blackhall Mosque", lat=55.96, lng=-3.25,
                     website_url="https://m.example/"))
    client = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text="ok")),
        follow_redirects=True,
    )

    out = discover_mosque(
        engine, "bh", fetcher=_fetcher, client=client, candidate_root=tmp_path,
        today=date(2026, 6, 1), horizon_days=40, renderer=lambda u: u,
        nav_renderer=_nav_renderer,
    )

    assert out.platform == "dom_records"
    assert out.outcome == "authored"

    with session_scope(engine) as s:
        src = repo.get_source(s, "bh")
        assert src.requires_js  # persisted truthy (SQLite int)
        assert '"shape":"dom_records"' in src.config
        assert '"mode":"render_nav"' in src.config
        june = {
            t.date: t.jamaah_time
            for t in repo.get_times(s, "bh", "2026-06-01", "2026-06-03")
            if t.prayer == "fajr"
        }
        july_prayers = {
            t.prayer for t in repo.get_times(s, "bh", "2026-07-01", "2026-07-02")
        }
        fajr_begin = [
            t.begin_time
            for t in repo.get_times(s, "bh", "2026-06-01", "2026-06-01")
            if t.prayer == "fajr"
        ]
    # Current month from the live render; the next month from month-nav paging.
    assert june["2026-06-01"] == "01:45"
    assert july_prayers == {"fajr", "dhuhr", "asr", "maghrib", "isha"}
    # The earlier of two times is captured as the begin/adhan.
    assert fajr_begin == ["01:33"]
