# tests/test_authoring_e2e.py
import json
from datetime import date

from directory import repository as repo
from directory.db import session_scope
from directory.ingest.author import run_authoring
from directory.ingest.candidate_store import save_bundle
from directory.ingest.discover import Candidate, CandidateBundle
from directory.ingest.fetch import FetchResult
from directory.models import Mosque, Source
from tests.conftest import FakeHarness

TABLE_HTML = (
    "<table class='t'><tr><th>Date</th><th>Fajr</th><th>Dhuhr</th><th>Asr</th>"
    "<th>Maghrib</th><th>Isha</th></tr>"
    "<tr><td>1 June</td><td>05:00</td><td>13:30</td><td>18:30</td><td>21:30</td><td>23:00</td></tr>"
    "<tr><td>2 June</td><td>05:02</td><td>13:31</td><td>18:31</td><td>21:31</td><td>23:01</td></tr>"
    "<tr><td>3 June</td><td>05:03</td><td>13:32</td><td>18:32</td><td>21:32</td><td>23:02</td></tr>"
    "</table>"
)
OUTPUT = json.dumps({
    "url": "https://m.example/prayer-times",
    "config": {"shape": "html_table", "grid": {
        "table_selector": "table.t", "date": {"index": 0}, "columns": [
            {"kind": "jamaah", "prayer": "fajr", "index": 1},
            {"kind": "jamaah", "prayer": "dhuhr", "index": 2},
            {"kind": "jamaah", "prayer": "asr", "index": 3},
            {"kind": "jamaah", "prayer": "maghrib", "index": 4},
            {"kind": "jamaah", "prayer": "isha", "index": 5}]}},
})


def _fetcher(url, **kwargs):
    return FetchResult(url, 200, TABLE_HTML, "h", error=None)


def test_candidate_authored_then_queryable(engine, tmp_path):
    with session_scope(engine) as s:
        s.add(Mosque(id="m", name="M", city="London", lat=52.0, lng=-1.0,
                     website_url="https://m.example/"))
        s.add(Source(id="m", mosque_id="m", url="https://m.example/prayer-times",
                     triage_status="candidate"))
    save_bundle(
        CandidateBundle("m", "https://m.example/",
                        [Candidate("https://m.example/prayer-times", 9.0, TABLE_HTML, "Fajr")]),
        root=tmp_path,
    )

    outs = run_authoring(engine, harness=FakeHarness(OUTPUT), candidate_root=tmp_path,
                         models=("cheap", "strong"), today=date(2026, 6, 1), horizon_days=5,
                         fetcher=_fetcher)

    assert [o.outcome for o in outs] == ["authored"]
    with session_scope(engine) as s:
        times = repo.get_times(s, "m", "2026-06-01", "2026-06-03")
        fajr = {t.jamaah_time for t in times if t.prayer == "fajr"}
    assert fajr == {"05:00", "05:02", "05:03"}
