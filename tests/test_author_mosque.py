# tests/test_author_mosque.py
import json
from datetime import date

from directory import repository as repo
from directory.db import session_scope
from directory.ingest.author import author_mosque
from directory.ingest.candidate_store import save_bundle
from directory.ingest.discover import Candidate, CandidateBundle
from directory.ingest.fetch import FetchResult
from directory.models import Mosque, Occurrence, Source
from tests.conftest import FakeHarness

TABLE_HTML = (
    "<table class='t'><tr><th>Date</th><th>Fajr</th><th>Dhuhr</th><th>Asr</th>"
    "<th>Maghrib</th><th>Isha</th></tr>"
    "<tr><td>1 June</td><td>05:00</td><td>13:30</td><td>18:30</td><td>21:30</td><td>23:00</td></tr>"
    "<tr><td>2 June</td><td>05:02</td><td>13:31</td><td>18:31</td><td>21:31</td><td>23:01</td></tr>"
    "</table>"
)

GOOD_OUTPUT = json.dumps(
    {
        "url": "https://m1.example/prayer-times",
        "config": {
            "shape": "html_table",
            "grid": {
                "table_selector": "table.t",
                "date": {"index": 0},
                "columns": [
                    {"kind": "jamaah", "prayer": "fajr", "index": 1},
                    {"kind": "jamaah", "prayer": "dhuhr", "index": 2},
                    {"kind": "jamaah", "prayer": "asr", "index": 3},
                    {"kind": "jamaah", "prayer": "maghrib", "index": 4},
                    {"kind": "jamaah", "prayer": "isha", "index": 5},
                ],
            },
        },
    }
)


def _candidate_mosque(engine, mid="m1"):
    with session_scope(engine) as s:
        s.add(Mosque(id=mid, name="M1", lat=52.0, lng=-1.0, website_url="https://m1.example/"))
        s.add(Source(id=mid, mosque_id=mid, url="https://m1.example/prayer-times",
                     triage_status="candidate"))


def _bundle(mid="m1"):
    return CandidateBundle(
        mid, "https://m1.example/",
        [Candidate("https://m1.example/prayer-times", 9.0, TABLE_HTML, "Fajr 05:00")],
    )


def _fetcher(url, **kwargs):
    return FetchResult(url, 200, TABLE_HTML, "h", error=None)


def test_authors_and_writes_occurrences(engine, tmp_path):
    _candidate_mosque(engine)
    save_bundle(_bundle(), root=tmp_path)
    harness = FakeHarness(GOOD_OUTPUT)

    out = author_mosque(engine, "m1", harness=harness, candidate_root=tmp_path,
                        models=("cheap", "strong"), today=date(2026, 6, 1), horizon_days=5,
                        fetcher=_fetcher)

    assert out.outcome == "authored"
    assert out.model == "cheap"          # cheap model succeeded; no escalation
    assert harness.calls == ["cheap"]
    with session_scope(engine) as s:
        assert s.query(Occurrence).count() > 0
        src = repo.get_source(s, "m1")
        assert src.triage_status == "authored"
        assert src.authored_by == "fake:cheap"
        assert src.authored_at is not None


def test_escalates_to_strong_when_cheap_output_is_garbage(engine, tmp_path):
    _candidate_mosque(engine)
    save_bundle(_bundle(), root=tmp_path)
    harness = FakeHarness({"cheap": "sorry, I cannot help", "strong": GOOD_OUTPUT})

    out = author_mosque(engine, "m1", harness=harness, candidate_root=tmp_path,
                        models=("cheap", "strong"), today=date(2026, 6, 1), horizon_days=5,
                        fetcher=_fetcher)

    assert out.outcome == "authored"
    assert out.model == "strong"
    assert harness.calls == ["cheap", "strong"]


def test_all_models_fail_marks_needs_reauthor(engine, tmp_path):
    _candidate_mosque(engine)
    save_bundle(_bundle(), root=tmp_path)
    harness = FakeHarness("not json at all")

    out = author_mosque(engine, "m1", harness=harness, candidate_root=tmp_path,
                        models=("cheap", "strong"), today=date(2026, 6, 1), horizon_days=5,
                        fetcher=_fetcher)

    assert out.outcome == "needs_reauthor"
    assert harness.calls == ["cheap", "strong"]
    with session_scope(engine) as s:
        assert repo.get_source(s, "m1").triage_status == "needs_reauthor"


def test_no_bundle_marks_no_candidate_without_calling_harness(engine, tmp_path):
    _candidate_mosque(engine)  # no save_bundle
    harness = FakeHarness(GOOD_OUTPUT)

    out = author_mosque(engine, "m1", harness=harness, candidate_root=tmp_path,
                        models=("cheap", "strong"), today=date(2026, 6, 1), horizon_days=5)

    assert out.outcome == "no_candidate"
    assert harness.calls == []
    with session_scope(engine) as s:
        assert repo.get_source(s, "m1").triage_status == "no_timetable"


def test_skips_non_candidate_source(engine, tmp_path):
    with session_scope(engine) as s:
        s.add(Mosque(id="m1", name="M1", lat=52.0, lng=-1.0))
        s.add(Source(id="m1", mosque_id="m1", triage_status="authored"))
    out = author_mosque(engine, "m1", harness=FakeHarness(GOOD_OUTPUT), candidate_root=tmp_path,
                        models=("cheap",))
    assert out.outcome == "skipped"
