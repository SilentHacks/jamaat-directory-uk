import re
from datetime import date
from pathlib import Path

import httpx

from directory import repository as repo
from directory.db import session_scope
from directory.ingest.discover import discover_mosque
from directory.ingest.fetch import FetchResult
from directory.models import Mosque

FIXTURES = Path(__file__).parent.parent / "fixtures"
WIDGET = (FIXTURES / "dpt_widget_page.html").read_text()
JUNE = (FIXTURES / "dpt_month_jun.html").read_text()
JULY = (FIXTURES / "dpt_month_jul.html").read_text()


def _fetcher(url, *, requires_js=False, etag=None, last_modified=None, client=None,
             renderer=None, timeout=20.0):
    # The Divi plugin's monthly grid: one clean <table> per month over admin-ajax.
    if "get_monthly_timetable" in url:
        m = re.search(r"month=(\d+)", url)
        month = int(m.group(1)) if m else 6
        return FetchResult(url, 200, JULY if month == 7 else JUNE, "h")
    # Every page of the site serves the single-day widget (incl. the signature).
    if url.startswith("https://m.example"):
        return FetchResult(url, 200, WIDGET, "h")
    return FetchResult(url, 404, "", None)


def test_dpt_site_authors_full_month_via_url_template(engine, tmp_path):
    with session_scope(engine) as s:
        s.add(Mosque(id="dpt", name="Divi Mosque", lat=55.99, lng=-3.78,
                     website_url="https://m.example/"))
    client = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text="ok")),
        follow_redirects=True,
    )

    out = discover_mosque(
        engine, "dpt", fetcher=_fetcher, client=client, candidate_root=tmp_path,
        today=date(2026, 6, 1), horizon_days=40,
    )

    assert out.platform == "wp_dpt"
    assert out.outcome == "authored"

    with session_scope(engine) as s:
        src = repo.get_source(s, "dpt")
        assert not src.requires_js  # £0: plain HTTP, no headless browser
        assert '"mode":"url_template"' in src.config
        assert "get_monthly_timetable" in src.config

        # Daily Fajr jamaah (iqamah) differs across the month boundary → not constant.
        jun_fajr = {
            t.jamaah_time for t in repo.get_times(s, "dpt", "2026-06-01", "2026-06-06")
            if t.prayer == "fajr"
        }
        jul_fajr = {
            t.jamaah_time for t in repo.get_times(s, "dpt", "2026-07-01", "2026-07-06")
            if t.prayer == "fajr"
        }
        # Adhan/begin captured alongside jamaah.
        jun_begin = {
            t.begin_time for t in repo.get_times(s, "dpt", "2026-06-01", "2026-06-01")
            if t.prayer == "fajr"
        }
        # Jumu'ah materialised on a Friday, two sessions from the widget.
        jum = sorted(
            (t.session_idx, t.jamaah_time)
            for t in repo.get_times(s, "dpt", "2026-06-05", "2026-06-05")
            if t.prayer == "jumuah"
        )

    assert jun_fajr == {"03:45"}
    assert jul_fajr == {"04:00"}
    assert jun_begin == {"02:34"}
    assert [t for _, t in jum] == ["13:30", "14:30"]
