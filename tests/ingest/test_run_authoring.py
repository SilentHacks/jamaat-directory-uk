# tests/test_run_authoring.py
import json
from datetime import date

from directory import repository as repo
from directory.db import session_scope
from directory.ingest.author import order_by_city_size, run_authoring
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


def _good_output(url):
    return json.dumps({
        "url": url,
        "config": {
            "shape": "html_table",
            "grid": {"table_selector": "table.t", "date": {"index": 0}, "columns": [
                {"kind": "jamaah", "prayer": "fajr", "index": 1},
                {"kind": "jamaah", "prayer": "dhuhr", "index": 2},
                {"kind": "jamaah", "prayer": "asr", "index": 3},
                {"kind": "jamaah", "prayer": "maghrib", "index": 4},
                {"kind": "jamaah", "prayer": "isha", "index": 5}]}},
    })


def _candidate(engine, mid, city, root):
    url = f"https://{mid}.example/prayer-times"
    with session_scope(engine) as s:
        s.add(Mosque(id=mid, name=mid, city=city, lat=52.0, lng=-1.0,
                     website_url=f"https://{mid}.example/"))
        s.add(Source(id=mid, mosque_id=mid, url=url, triage_status="candidate"))
    CandidateBundle(mid, f"https://{mid}.example/",
                    [Candidate(url, 9.0, TABLE_HTML, "Fajr")]).save(root)
    return url


def _fetcher(url, **kwargs):
    return FetchResult(url, 200, TABLE_HTML, "h", error=None)


def test_order_by_city_size_puts_big_cities_first():
    ms = [
        Mosque(id="b", name="b", city="Small", lat=0, lng=0),
        Mosque(id="a", name="a", city="Big", lat=0, lng=0),
        Mosque(id="c", name="c", city="Big", lat=0, lng=0),
    ]
    ordered = order_by_city_size(ms)
    assert [m.id for m in ordered] == ["a", "c", "b"]  # Big (2) before Small (1), id tiebreak


def test_run_authoring_processes_all_candidates(engine, tmp_path):
    _candidate(engine, "m1", "London", tmp_path)
    _candidate(engine, "m2", "London", tmp_path)
    harness = FakeHarness({"cheap": _good_output("https://m1.example/prayer-times")})
    # per-mosque url differs, so use a harness that echoes the right url per model? Simpler:
    harness = FakeHarness(_good_output("https://m1.example/prayer-times"))

    outs = run_authoring(engine, harness=harness, candidate_root=tmp_path,
                         models=("cheap",), today=date(2026, 6, 1), horizon_days=5,
                         fetcher=_fetcher)

    assert len(outs) == 2
    assert all(o.outcome == "authored" for o in outs)


def test_budget_caps_spend_and_is_resumable(engine, tmp_path):
    _candidate(engine, "m1", "London", tmp_path)
    _candidate(engine, "m2", "London", tmp_path)
    harness = FakeHarness(_good_output("https://m1.example/prayer-times"))

    first = run_authoring(engine, harness=harness, candidate_root=tmp_path, models=("cheap",),
                          max_calls=1, today=date(2026, 6, 1), horizon_days=5, fetcher=_fetcher)
    assert len(first) == 1  # only one mosque consumed the budget

    with session_scope(engine) as s:
        remaining = [c.id for c in repo.candidate_sources(s)]
    assert len(remaining) == 1  # the other is still 'candidate' → resumable

    second = run_authoring(engine, harness=harness, candidate_root=tmp_path, models=("cheap",),
                           max_calls=5, today=date(2026, 6, 1), horizon_days=5, fetcher=_fetcher)
    assert len(second) == 1  # the previously-skipped one
    with session_scope(engine) as s:
        assert repo.candidate_sources(s) == []


def test_run_authoring_escalates_to_fallback(engine, tmp_path):
    _candidate(engine, "m1", "London", tmp_path)
    fallback = FakeBrowsingHarness(_good_output("https://m1.example/prayer-times"))

    outs = run_authoring(
        engine, harness=FakeHarness("garbage"), fallback=fallback, candidate_root=tmp_path,
        models=("cheap",), bespoke_root=tmp_path / "bespoke",
        today=date(2026, 6, 1), horizon_days=5, fetcher=_fetcher,
    )

    assert [o.outcome for o in outs] == ["authored"]
    assert fallback.calls == ["agentic"]
