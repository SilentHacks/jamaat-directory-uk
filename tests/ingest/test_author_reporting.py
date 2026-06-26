import json
from datetime import date

from directory import repository as repo
from directory.db import session_scope
from directory.ingest.author import (
    AuthorOutcome,
    author_mosque,
    categorize_outcome,
    diagnose_candidate,
    summarize_outcomes,
)
from directory.ingest.discover import Candidate, CandidateBundle
from directory.ingest.evidence import build_page_evidence
from directory.ingest.fetch import FetchResult
from directory.models import Mosque, Source
from tests.conftest import FakeBrowsingHarness, FakeHarness

TODAY = date(2026, 6, 1)

TABLE_HTML = (
    "<table class='t'><tr><th>Date</th><th>Fajr</th><th>Dhuhr</th><th>Asr</th>"
    "<th>Maghrib</th><th>Isha</th></tr>"
    "<tr><td>1 June</td><td>05:00</td><td>13:30</td><td>18:30</td><td>21:30</td><td>23:00</td></tr>"
    "<tr><td>2 June</td><td>05:02</td><td>13:31</td><td>18:31</td><td>21:31</td><td>23:01</td></tr>"
    "<tr><td>3 June</td><td>05:03</td><td>13:32</td><td>18:32</td><td>21:32</td><td>23:02</td></tr>"
    "</table>"
)
CONFIG_OUTPUT = json.dumps({
    "url": "https://m1.example/prayer-times",
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


def _candidate(engine, mid="m1"):
    with session_scope(engine) as s:
        s.add(Mosque(id=mid, name=mid, lat=52.0, lng=-1.0, website_url="https://m1.example/"))
        s.add(Source(id=mid, mosque_id=mid, url="https://m1.example/prayer-times",
                     triage_status="candidate"))


def _bundle(mid="m1"):
    # No evidence → the deterministic enumerator is skipped, so a model decides.
    return CandidateBundle(
        mid, "https://m1.example/",
        [Candidate("https://m1.example/prayer-times", 9.0, TABLE_HTML, "Fajr 05:00")],
    )


# ── categorize / summarize ────────────────────────────────────────────────────


def test_categorize_splits_deterministic_vs_model_authored():
    det = AuthorOutcome("m", "authored", model=None)
    mod = AuthorOutcome("m", "authored", model="opus")
    assert categorize_outcome(det) == "deterministic_authored"
    assert categorize_outcome(mod) == "model_authored"


def test_categorize_splits_wrong_site_from_no_timetable():
    no_tt = AuthorOutcome("m", "no_timetable", last_status="no_timetable")
    wrong = AuthorOutcome("m", "no_timetable", last_status="wrong_site")
    assert categorize_outcome(no_tt) == "no_timetable"
    assert categorize_outcome(wrong) == "wrong_site"


def test_categorize_passes_through_other_outcomes():
    raws = ("review", "deferred_media", "needs_reauthor", "skipped", "no_candidate", "no_model")
    for raw in raws:
        assert categorize_outcome(AuthorOutcome("m", raw)) == raw


def test_summarize_aggregates_buckets_and_call_counts():
    outcomes = [
        AuthorOutcome("a", "authored", model=None),                       # deterministic
        AuthorOutcome("b", "authored", model="opus", model_calls=1),      # model
        AuthorOutcome("c", "no_timetable", last_status="wrong_site", model_calls=1),
        AuthorOutcome("d", "needs_reauthor", model_calls=1, fallback_calls=1),
        AuthorOutcome("e", "deferred_media", model=None),
    ]
    tally = summarize_outcomes(outcomes)
    assert tally["deterministic_authored"] == 1
    assert tally["model_authored"] == 1
    assert tally["wrong_site"] == 1
    assert tally["needs_reauthor"] == 1
    assert tally["deferred_media"] == 1
    assert tally["model_calls"] == 3
    assert tally["fallback_calls"] == 1


# ── --no-model (deterministic-only) ───────────────────────────────────────────


def test_no_model_leaves_candidate_untouched_without_calling_model(engine, tmp_path):
    _candidate(engine)
    _bundle().save(tmp_path)
    harness = FakeHarness(CONFIG_OUTPUT)

    out = author_mosque(engine, "m1", harness=harness, candidate_root=tmp_path,
                        models=("cheap",), today=TODAY, horizon_days=5, fetcher=_fetcher,
                        no_model=True)

    assert out.outcome == "no_model"
    assert harness.calls == []  # the model was never invoked
    with session_scope(engine) as s:
        src = repo.get_source(s, "m1")
        assert src.triage_status == "candidate"  # left for a later paid run
        assert src.config is None


def test_no_model_still_authors_via_deterministic_enumerator(engine, tmp_path):
    # A bundle carrying evidence the enumerator can map is authored for £0 even with
    # the model disabled.
    _candidate(engine)
    ev = build_page_evidence(TABLE_HTML, "https://m1.example/prayer-times", today=TODAY)
    CandidateBundle(
        "m1", "https://m1.example/",
        [Candidate("https://m1.example/prayer-times", 9.0, TABLE_HTML, "Fajr 05:00")],
        evidence=[ev],
    ).save(tmp_path)
    harness = FakeHarness(CONFIG_OUTPUT)

    out = author_mosque(engine, "m1", harness=harness, candidate_root=tmp_path,
                        models=("cheap",), today=TODAY, horizon_days=5, fetcher=_fetcher,
                        no_model=True)

    assert out.outcome == "authored"
    assert out.model is None  # deterministic
    assert harness.calls == []


# ── model_calls / fallback_calls accounting ───────────────────────────────────


def test_model_authored_records_one_model_call(engine, tmp_path):
    _candidate(engine)
    _bundle().save(tmp_path)
    harness = FakeHarness(CONFIG_OUTPUT)

    out = author_mosque(engine, "m1", harness=harness, candidate_root=tmp_path,
                        models=("cheap",), today=TODAY, horizon_days=5, fetcher=_fetcher,
                        feedback_retries=0)

    assert out.outcome == "authored"
    assert out.model == "cheap"
    assert out.model_calls == 1
    assert out.fallback_calls == 0


def test_fallback_call_counted_separately(engine, tmp_path):
    _candidate(engine)
    _bundle().save(tmp_path)
    # Stage 1 (model) can't decide → escalates; stage 2 (fallback/browse) authors.
    stage1 = FakeHarness(json.dumps({"outcome": "unknown"}))
    fallback = FakeBrowsingHarness(CONFIG_OUTPUT)

    out = author_mosque(engine, "m1", harness=stage1, candidate_root=tmp_path,
                        models=("cheap",), fallback=fallback, fallback_model="browse",
                        today=TODAY, horizon_days=5, fetcher=_fetcher, feedback_retries=0)

    assert out.outcome == "authored"
    assert out.model_calls == 1      # the single-shot stage
    assert out.fallback_calls == 1   # the browsing stage


# ── diagnose_candidate (dry-run inspection) ───────────────────────────────────

WIDGET_HTML = (
    "<html><body><h2>Prayer Times</h2>"
    "<iframe src='https://mawaqit.net/en/m/example-masjid'></iframe></body></html>"
)


def test_diagnose_reports_widget_routing_when_not_recovered(tmp_path):
    # An iframe-widget page: no registered widget extractor verifies it, so the
    # deterministic pass cannot recover and a model would get the widget prompt.
    ev = build_page_evidence(WIDGET_HTML, "https://m1.example/", today=TODAY)
    CandidateBundle(
        "m1", "https://m1.example/",
        [Candidate("https://m1.example/", 1.0, WIDGET_HTML, "Prayer Times")],
        evidence=[ev],
    ).save(tmp_path)

    report = diagnose_candidate(None, "m1", candidate_root=tmp_path, today=TODAY,
                                horizon_days=5, fetcher=_fetcher)

    assert report.found_bundle
    assert report.pages[0].page_class == "iframe_or_widget"
    assert report.pages[0].n_iframes == 1
    assert not report.deterministic_recovered
    assert report.prompt_kind == "widget"


def test_diagnose_reports_recovery_for_mappable_table(tmp_path):
    ev = build_page_evidence(TABLE_HTML, "https://m1.example/prayer-times", today=TODAY)
    CandidateBundle(
        "m1", "https://m1.example/",
        [Candidate("https://m1.example/prayer-times", 9.0, TABLE_HTML, "Fajr 05:00")],
        evidence=[ev],
    ).save(tmp_path)

    report = diagnose_candidate(None, "m1", candidate_root=tmp_path, today=TODAY,
                                horizon_days=5, fetcher=_fetcher)

    assert report.deterministic_recovered
    assert report.prompt_kind == "none"
    assert any(c.ok for c in report.candidates)


def test_diagnose_no_bundle(tmp_path):
    report = diagnose_candidate(None, "ghost", candidate_root=tmp_path)
    assert report.found_bundle is False
    assert report.prompt_kind == "none"
