from datetime import date
from pathlib import Path

import httpx

from directory import repository as repo
from directory.db import session_scope
from directory.ingest.discover import discover_mosque
from directory.ingest.fetch import FetchResult
from directory.models import Mosque

FIXTURE = Path(__file__).parent.parent / "fixtures" / "azhar_month_tables.html"
TIMETABLE = FIXTURE.read_text()
HOME = (
    '<html><body><nav><a href="/old/prayer-timetable/">Prayer Timetable</a>'
    "</nav></body></html>"
)


def _fetcher(url, *, requires_js=False, etag=None, last_modified=None, client=None,
             renderer=None, timeout=20.0):
    body = TIMETABLE if "prayer-timetable" in url else HOME
    return FetchResult(url, 200, body, "hash")


def test_azhar_month_table_site_authored_and_queryable(engine, tmp_path):
    # The Azhar shape (annual page of <td> month tables) must discover, author,
    # and serve times for both the current and a later month — deterministically.
    with session_scope(engine) as s:
        s.add(Mosque(id="azhar", name="Azhar Masjid", lat=51.5, lng=-0.0,
                     website_url="https://masjid.example/old/"))
    client = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text="ok")),
        follow_redirects=True,
    )

    out = discover_mosque(engine, "azhar", fetcher=_fetcher, client=client,
                          candidate_root=tmp_path, today=date(2026, 6, 1), horizon_days=60)

    assert out.platform == "generic_table"
    assert out.outcome == "authored"

    with session_scope(engine) as s:
        src = repo.get_source(s, "azhar")
        assert src.config and '"month_sections":true' in src.config
        # Times land for the current month and the next month from one page.
        june_fajr = {
            t.date: t.jamaah_time
            for t in repo.get_times(s, "azhar", "2026-06-01", "2026-06-03")
            if t.prayer == "fajr"
        }
        july_prayers = {
            t.prayer for t in repo.get_times(s, "azhar", "2026-07-01", "2026-07-03")
        }
    assert june_fajr["2026-06-01"] == "04:25"
    assert july_prayers == {"fajr", "dhuhr", "asr", "maghrib", "isha"}
