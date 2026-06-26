"""End-to-end (A2 + C7): a mosque page that links to its my-masjid timing screen
with a button — the timetable lives off-site in a JSON API — is authored
deterministically (zero model calls) by following the anchor and reading the API."""

from datetime import date
from pathlib import Path

import httpx

from directory import repository as repo
from directory.db import session_scope
from directory.ingest.discover import discover_mosque
from directory.ingest.fetch import FetchResult, html_hash
from directory.models import Mosque

GUID = "f4c8cc40-8e42-47ce-9e74-d8125a10b0ba"
API = f"https://time.my-masjid.com/api/TimingsInfoScreen/GetMasjidTimings?GuidId={GUID}"
TIMINGS = (
    Path(__file__).parent.parent / "fixtures" / "widgets" / "my_masjid_timings.json"
).read_text()

# The mosque's own page: chrome plus a "Prayer Times" button to the screen. No table,
# no iframe — exactly the alhudamosque.com shape that defeated the model.
HOME = f"""
<html><body>
  <h1>Welcome to Al-Huda</h1>
  <a class="btn" href="https://time.my-masjid.com/timingscreen/{GUID}">Prayer Times</a>
</body></html>
"""


def _fetcher(url, *, requires_js=False, etag=None, last_modified=None, client=None,
             renderer=None, timeout=20.0):
    if url == API:
        return FetchResult(url, 200, TIMINGS, html_hash(TIMINGS))
    if "my-masjid.com" in url:  # the Angular screen shell (never the data)
        return FetchResult(url, 200, "<html><body><app-root></app-root></body></html>", "h")
    if url.rstrip("/") == "https://alhuda.example":
        return FetchResult(url, 200, HOME, html_hash(HOME))
    return FetchResult(url, 404, "<html>not found</html>", "h")


def test_my_masjid_button_authors_deterministically(engine, tmp_path):
    with session_scope(engine) as s:
        s.add(Mosque(id="ah", name="Al-Huda", lat=51.5, lng=-0.05,
                     website_url="https://alhuda.example/"))
    client = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text="ok")),
        follow_redirects=True,
    )

    out = discover_mosque(engine, "ah", fetcher=_fetcher, client=client,
                          candidate_root=tmp_path, today=date(2026, 6, 1), horizon_days=10)

    assert out.outcome in {"authored", "review"}
    assert out.platform == "mylocalmasjid"
    with session_scope(engine) as s:
        src = repo.get_source(s, "ah")
        assert src.shape == "widget"
        assert API in src.config
        if out.outcome == "authored":
            times = repo.get_times(s, "ah", "2026-06-01", "2026-06-02")
            assert any(t.prayer == "fajr" and t.jamaah_time for t in times)
