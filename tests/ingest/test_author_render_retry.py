# tests/ingest/test_author_render_retry.py
import json
from datetime import date

from directory import repository as repo
from directory.db import session_scope
from directory.ingest.author import author_mosque
from directory.ingest.discover import Candidate, CandidateBundle
from directory.ingest.fetch import FetchResult
from directory.models import Mosque, Source
from tests.conftest import FakeHarness

TABLE_HTML = (
    "<table class='t'><tr><th>Date</th><th>Fajr</th><th>Dhuhr</th><th>Asr</th>"
    "<th>Maghrib</th><th>Isha</th></tr>"
    "<tr><td>1 June</td><td>05:00</td><td>13:30</td><td>18:30</td><td>21:30</td><td>23:00</td></tr>"
    "<tr><td>2 June</td><td>05:02</td><td>13:31</td><td>18:31</td><td>21:31</td><td>23:01</td></tr>"
    "</table>"
)


def _good_output(url):
    return json.dumps({
        "url": url,
        "config": {"shape": "html_table", "grid": {
            "table_selector": "table.t", "date": {"index": 0}, "columns": [
                {"kind": "jamaah", "prayer": "fajr", "index": 1},
                {"kind": "jamaah", "prayer": "dhuhr", "index": 2},
                {"kind": "jamaah", "prayer": "asr", "index": 3},
                {"kind": "jamaah", "prayer": "maghrib", "index": 4},
                {"kind": "jamaah", "prayer": "isha", "index": 5}]}},
    })


def _candidate(engine, mid, root):
    url = f"https://{mid}.example/prayer-times"
    with session_scope(engine) as s:
        s.add(Mosque(id=mid, name=mid, city="X", lat=52.0, lng=-1.0,
                     website_url=f"https://{mid}.example/"))
        s.add(Source(id=mid, mosque_id=mid, url=url, triage_status="candidate"))
    CandidateBundle(mid, f"https://{mid}.example/",
                    [Candidate(url, 9.0, TABLE_HTML, "Fajr")]).save(root)
    return url


def _render_sensitive_fetcher(url, *, requires_js=False, renderer=None, **kwargs):
    """The timetable only appears when the page is rendered (requires_js=True);
    a static fetch returns a JS shell with no table."""
    html = TABLE_HTML if requires_js else "<html><body>loading…</body></html>"
    return FetchResult(url, 200, html, "h", error=None)


def test_author_retries_verify_with_render_on_zero_rows(engine, tmp_path):
    url = _candidate(engine, "m1", tmp_path)
    out = author_mosque(
        engine, "m1", harness=FakeHarness(_good_output(url)), candidate_root=tmp_path,
        models=("opus@low",), today=date(2026, 6, 1), horizon_days=5,
        fetcher=_render_sensitive_fetcher, renderer=object(),  # truthy renderer enables retry
    )
    assert out.outcome == "authored"
    with session_scope(engine) as s:
        assert repo.get_source(s, "m1").requires_js == 1  # promoted to a JS source


def test_author_no_render_retry_when_renderer_absent(engine, tmp_path):
    url = _candidate(engine, "m2", tmp_path)
    out = author_mosque(
        engine, "m2", harness=FakeHarness(_good_output(url)), candidate_root=tmp_path,
        models=("opus@low",), today=date(2026, 6, 1), horizon_days=5,
        fetcher=_render_sensitive_fetcher, renderer=None,  # --no-render-js: no retry
    )
    assert out.outcome == "needs_reauthor"
