from datetime import date

import httpx

from directory import repository as repo
from directory.db import session_scope
from directory.ingest.discover import discover_mosque
from directory.ingest.fetch import FetchResult
from directory.models import Mosque

WP_HTML = """
<html><head><meta name="generator" content="WordPress"></head><body>
<table class="dpt_table">
  <tr><th>Date</th><th>Fajr</th><th>Dhuhr</th><th>Asr</th><th>Maghrib</th><th>Isha</th></tr>
  <tr><td>1</td><td>05:00</td><td>13:15</td><td>18:30</td><td>21:10</td><td>22:30</td></tr>
  <tr><td>2</td><td>05:02</td><td>13:16</td><td>18:31</td><td>21:11</td><td>22:31</td></tr>
  <tr><td>3</td><td>05:03</td><td>13:17</td><td>18:32</td><td>21:12</td><td>22:32</td></tr>
</table></body></html>
"""


def _fetcher(url, *, requires_js=False, etag=None, last_modified=None, client=None,
             renderer=None, timeout=20.0):
    return FetchResult(url, 200, WP_HTML, "hash")


def test_discovery_produces_api_queryable_times(engine, tmp_path):
    with session_scope(engine) as s:
        s.add(Mosque(id="wp", name="WP Masjid", lat=52.0, lng=-1.0,
                     website_url="https://wp.example/"))
    client = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text="ok")),
        follow_redirects=True,
    )

    out = discover_mosque(engine, "wp", fetcher=_fetcher, client=client,
                          candidate_root=tmp_path, today=date(2026, 6, 1), horizon_days=10)

    assert out.platform == "wp_prayer"
    assert out.outcome in {"authored", "review"}

    if out.outcome == "authored":
        with session_scope(engine) as s:
            times = repo.get_times(s, "wp", "2026-06-01", "2026-06-03")
            fajr = [t for t in times if t.prayer == "fajr"]
            assert {t.jamaah_time for t in fajr} == {"05:00", "05:02", "05:03"}
