# tests/test_run_authoring.py
import json
from datetime import date

import pytest

from directory import repository as repo
from directory.db import session_scope
from directory.ingest.author import order_by_city_size, run_authoring
from directory.ingest.discover import Candidate, CandidateBundle
from directory.ingest.fetch import FetchResult
from directory.ingest.harness import HarnessResult, reset_shutdown
from directory.models import Mosque, Source
from tests.conftest import FakeBrowsingHarness, FakeHarness


class _InterruptingHarness:
    """AuthorHarness double that raises KeyboardInterrupt, simulating an operator
    Ctrl-C landing while the agent call is in flight."""

    name = "fake"

    def run(self, prompt: str, *, model: str) -> HarnessResult:
        raise KeyboardInterrupt

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


def test_run_authoring_reports_live_progress(engine, tmp_path):
    _candidate(engine, "m1", "London", tmp_path)
    _candidate(engine, "m2", "London", tmp_path)
    harness = FakeHarness(_good_output("https://m1.example/prayer-times"))

    calls = []
    run_authoring(
        engine, harness=harness, candidate_root=tmp_path, models=("cheap",),
        today=date(2026, 6, 1), horizon_days=5, fetcher=_fetcher,
        on_outcome=lambda done, total, out: calls.append((done, total, out.mosque_id)),
    )

    assert [done for done, _, _ in calls] == [1, 2]  # live, sequential progress
    assert {total for _, total, _ in calls} == {2}  # total known up front
    assert {mid for _, _, mid in calls} == {"m1", "m2"}


def test_interrupt_leaves_sources_candidate_and_resumes(engine, tmp_path):
    _candidate(engine, "m1", "London", tmp_path)
    _candidate(engine, "m2", "London", tmp_path)
    try:
        with pytest.raises(KeyboardInterrupt):
            run_authoring(
                engine, harness=_InterruptingHarness(), candidate_root=tmp_path,
                models=("cheap",), concurrency=1, today=date(2026, 6, 1), horizon_days=5,
                fetcher=_fetcher,
            )
        # interrupted in-flight + never-dispatched both remain candidate → resumable
        with session_scope(engine) as s:
            assert {c.id for c in repo.candidate_sources(s)} == {"m1", "m2"}
    finally:
        reset_shutdown()  # the run latched a shutdown; clear it for the resume

    good = FakeHarness(_good_output("https://m1.example/prayer-times"))
    outs = run_authoring(engine, harness=good, candidate_root=tmp_path, models=("cheap",),
                         today=date(2026, 6, 1), horizon_days=5, fetcher=_fetcher)
    assert sorted(o.outcome for o in outs) == ["authored", "authored"]
    with session_scope(engine) as s:
        assert repo.candidate_sources(s) == []


def test_interrupt_during_verify_rolls_back_to_candidate(engine, tmp_path):
    _candidate(engine, "m1", "London", tmp_path)

    def _boom_fetch(url, **kwargs):
        # Ctrl-C during the verify fetch, *after* a provisional 'authored' write
        raise KeyboardInterrupt

    good = FakeHarness(_good_output("https://m1.example/prayer-times"))
    try:
        with pytest.raises(KeyboardInterrupt):
            run_authoring(engine, harness=good, candidate_root=tmp_path, models=("cheap",),
                          concurrency=1, today=date(2026, 6, 1), horizon_days=5,
                          fetcher=_boom_fetch)
        # rolled back: not left stranded as a half-verified 'authored'
        with session_scope(engine) as s:
            src = repo.get_source(s, "m1")
            assert src.triage_status == "candidate"
            assert src.config is None
    finally:
        reset_shutdown()


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


def test_budget_unit_reserve_refund_caps():
    from directory.ingest.author import Budget

    b = Budget(2)
    assert b.try_reserve() is True
    assert b.try_reserve() is True
    assert b.try_reserve() is False  # cap reached
    b.refund()
    assert b.try_reserve() is True  # slot freed
    assert b.spent == 2


def test_budget_caps_chargeable_calls_under_concurrency(engine, tmp_path):
    for i in range(20):
        _candidate(engine, f"m{i:02d}", "London", tmp_path)
    harness = FakeHarness(_good_output("https://x.example/prayer-times"))

    outs = run_authoring(engine, harness=harness, candidate_root=tmp_path, models=("cheap",),
                         max_calls=5, concurrency=8, today=date(2026, 6, 1), horizon_days=5,
                         fetcher=_fetcher)

    chargeable = [o for o in outs if o.outcome not in {"no_candidate", "skipped"}]
    assert len(chargeable) <= 5  # spend cap holds under concurrency
    assert len(harness.calls) <= 5  # no more than max_calls paid harness invocations
    with session_scope(engine) as s:
        # the un-budgeted candidates remain resumable
        assert len(repo.candidate_sources(s)) >= 15


def test_budget_not_consumed_by_free_deterministic_recovery(engine, tmp_path):
    # A bundle the enumerator can map is authored for £0. A free deterministic win
    # makes no chargeable call, so it must NOT burn a budget slot — otherwise a
    # small --max-calls cap silently strands the rest of the corpus. (Regression:
    # the cap counts model spend, not the outcome bucket.)
    from directory.ingest.evidence import build_page_evidence

    for mid in ("m1", "m2", "m3"):
        _candidate(engine, mid, "London", tmp_path)
        url = f"https://{mid}.example/prayer-times"
        ev = build_page_evidence(TABLE_HTML, url, today=date(2026, 6, 1))
        CandidateBundle(mid, f"https://{mid}.example/",
                        [Candidate(url, 9.0, TABLE_HTML, "Fajr")], evidence=[ev]).save(tmp_path)
    harness = FakeHarness(_good_output("https://m1.example/prayer-times"))

    # concurrency=1 so the reserve→refund cycle is sequential (a refunded slot is
    # observable by the next worker, not lost to a concurrent reserve race).
    outs = run_authoring(engine, harness=harness, candidate_root=tmp_path, models=("cheap",),
                         max_calls=1, concurrency=1, today=date(2026, 6, 1), horizon_days=5,
                         fetcher=_fetcher)

    assert len(outs) == 3  # all recovered despite max_calls=1 — budget never spent
    assert all(o.outcome == "authored" and o.model is None for o in outs)
    assert harness.calls == []  # the model was never paid


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
