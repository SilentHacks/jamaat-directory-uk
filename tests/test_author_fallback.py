import json
from datetime import date

from directory import repository as repo
from directory.db import session_scope
from directory.ingest.author import author_mosque
from directory.ingest.candidate_store import save_bundle
from directory.ingest.discover import Candidate, CandidateBundle
from directory.ingest.fetch import FetchResult
from directory.models import Mosque, Source
from tests.conftest import FakeBrowsingHarness, FakeHarness

TABLE_HTML = (
    "<table class='t'><tr><th>Date</th><th>Fajr</th><th>Dhuhr</th><th>Asr</th>"
    "<th>Maghrib</th><th>Isha</th></tr>"
    "<tr><td>1 June</td><td>05:00</td><td>13:30</td><td>18:30</td><td>21:30</td><td>23:00</td></tr>"
    "<tr><td>2 June</td><td>05:02</td><td>13:31</td><td>18:31</td><td>21:31</td><td>23:01</td></tr>"
    "</table>"
)

STD_OUTPUT = json.dumps({
    "url": "https://m1.example/prayer-times",
    "config": {"shape": "html_table", "grid": {
        "table_selector": "table.t", "date": {"index": 0}, "columns": [
            {"kind": "jamaah", "prayer": "fajr", "index": 1},
            {"kind": "jamaah", "prayer": "dhuhr", "index": 2},
            {"kind": "jamaah", "prayer": "asr", "index": 3},
            {"kind": "jamaah", "prayer": "maghrib", "index": 4},
            {"kind": "jamaah", "prayer": "isha", "index": 5}]}},
})


def _candidate_mosque(engine, mid="m1"):
    with session_scope(engine) as s:
        s.add(Mosque(id=mid, name="M1", lat=52.0, lng=-1.0, website_url="https://m1.example/"))
        s.add(Source(id=mid, mosque_id=mid, url="https://m1.example/prayer-times",
                     triage_status="candidate"))


def _bundle(mid="m1"):
    return CandidateBundle(mid, "https://m1.example/",
                           [Candidate("https://m1.example/prayer-times", 9.0, TABLE_HTML, "Fajr")])


def _fetcher(url, **kwargs):
    return FetchResult(url, 200, TABLE_HTML, "h", error=None)


def test_fallback_authors_when_single_shot_fails(engine, tmp_path):
    _candidate_mosque(engine)
    save_bundle(_bundle(), root=tmp_path)
    single = FakeHarness("garbage, not json")
    fallback = FakeBrowsingHarness(STD_OUTPUT)

    out = author_mosque(
        engine, "m1", harness=single, candidate_root=tmp_path, models=("cheap", "strong"),
        fallback=fallback, bespoke_root=tmp_path / "bespoke",
        today=date(2026, 6, 1), horizon_days=5, fetcher=_fetcher,
    )

    assert out.outcome == "authored"
    assert single.calls == ["cheap", "strong"]   # both single-shot models tried first
    assert fallback.calls == ["agentic"]          # then one agentic attempt
    with session_scope(engine) as s:
        src = repo.get_source(s, "m1")
        assert src.triage_status == "authored"
        assert src.authored_by == "fake-agentic:agentic"


def test_no_fallback_keeps_phase4_behaviour(engine, tmp_path):
    _candidate_mosque(engine)
    save_bundle(_bundle(), root=tmp_path)

    out = author_mosque(
        engine, "m1", harness=FakeHarness("not json at all"), candidate_root=tmp_path,
        models=("cheap", "strong"), today=date(2026, 6, 1), horizon_days=5, fetcher=_fetcher,
    )

    assert out.outcome == "needs_reauthor"  # no fallback supplied → unchanged
