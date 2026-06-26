# tests/ingest/test_reauthor.py
import json
from datetime import date

from directory import repository as repo
from directory.db import session_scope
from directory.ingest.author import run_reauthor, run_verify_retry
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

GOOD_CONFIG = json.dumps({
    "shape": "html_table",
    "grid": {"table_selector": "table.t", "date": {"index": 0}, "columns": [
        {"kind": "jamaah", "prayer": "fajr", "index": 1},
        {"kind": "jamaah", "prayer": "dhuhr", "index": 2},
        {"kind": "jamaah", "prayer": "asr", "index": 3},
        {"kind": "jamaah", "prayer": "maghrib", "index": 4},
        {"kind": "jamaah", "prayer": "isha", "index": 5}]},
})


def _src(engine, mid, config, status="needs_reauthor"):
    with session_scope(engine) as s:
        s.add(Mosque(id=mid, name=mid, lat=52.0, lng=-1.0,
                     website_url=f"https://{mid}.example/"))
        s.add(Source(id=mid, mosque_id=mid, url=f"https://{mid}.example/t",
                     shape="html_table", config=config, triage_status=status))


def _fetcher(url, **kwargs):
    return FetchResult(url, 200, TABLE_HTML, "h", error=None)


def test_verify_retry_promotes_salvageable_config(engine):
    """A correct config that previously failed (e.g. a flaky fetch) is promoted
    on a clean re-extraction — no model call."""
    _src(engine, "m1", GOOD_CONFIG)
    outs = run_verify_retry(engine, today=date(2026, 6, 1), horizon_days=5, fetcher=_fetcher)
    assert [o.triage_status for o in outs] == ["authored"]
    with session_scope(engine) as s:
        assert repo.get_source(s, "m1").triage_status == "authored"


def test_verify_retry_skips_configless_sources(engine):
    """A needs_reauthor source whose config was nulled is not a verify-retry
    target — there is nothing to re-extract."""
    with session_scope(engine) as s:
        s.add(Mosque(id="nc", name="nc", lat=52.0, lng=-1.0))
        s.add(Source(id="nc", mosque_id="nc", url="https://nc.example/",
                     config=None, triage_status="needs_reauthor"))
    outs = run_verify_retry(engine, today=date(2026, 6, 1), horizon_days=5, fetcher=_fetcher)
    assert outs == []


def test_verify_retry_leaves_genuine_failure_as_needs_reauthor(engine):
    """A config that still does not extract stays needs_reauthor — verify-retry
    never demotes or wipes it."""
    _src(engine, "bad", GOOD_CONFIG)

    def _empty(url, **kwargs):
        return FetchResult(url, 200, "<html><body>no table</body></html>", "h", error=None)

    outs = run_verify_retry(engine, today=date(2026, 6, 1), horizon_days=5, fetcher=_empty)
    assert [o.triage_status for o in outs] == ["needs_reauthor"]
    with session_scope(engine) as s:
        src = repo.get_source(s, "bad")
        assert src.triage_status == "needs_reauthor"
        assert src.config == GOOD_CONFIG  # config retained for a future retry


def _good_output(url):
    return json.dumps({"url": url, "config": json.loads(GOOD_CONFIG)})


def _bad_valid_output(url):
    cfg = json.loads(GOOD_CONFIG)
    cfg["grid"]["table_selector"] = "table.nope"  # valid schema, matches nothing
    return json.dumps({"url": url, "config": cfg})


def _reauthor_candidate(engine, mid, root, *, config):
    url = f"https://{mid}.example/prayer-times"
    with session_scope(engine) as s:
        s.add(Mosque(id=mid, name=mid, city="X", lat=52.0, lng=-1.0,
                     website_url=f"https://{mid}.example/"))
        s.add(Source(id=mid, mosque_id=mid, url=url, shape="html_table",
                     config=config, triage_status="needs_reauthor"))
    CandidateBundle(mid, f"https://{mid}.example/",
                    [Candidate(url, 9.0, TABLE_HTML, "Fajr")]).save(root)
    return url


def test_run_reauthor_promotes_with_model(engine, tmp_path):
    url = _reauthor_candidate(engine, "m1", tmp_path, config='{"shape":"html_table"}')
    harness = FakeHarness(_good_output(url))
    outs = run_reauthor(engine, harness=harness, candidate_root=tmp_path,
                        models=("opus@low",), today=date(2026, 6, 1), horizon_days=5,
                        fetcher=_fetcher)
    assert [o.outcome for o in outs] == ["authored"]
    assert harness.calls == ["opus@low"]


def test_run_reauthor_restores_prior_config_on_failure(engine, tmp_path):
    """A non-deterministic model that regenerates a worse (valid-but-rejected)
    config must not be allowed to discard the prior config."""
    url = _reauthor_candidate(engine, "m1", tmp_path, config=GOOD_CONFIG)
    harness = FakeHarness(_bad_valid_output(url))
    outs = run_reauthor(engine, harness=harness, candidate_root=tmp_path,
                        models=("opus@low",), today=date(2026, 6, 1), horizon_days=5,
                        fetcher=_fetcher)
    assert [o.outcome for o in outs] == ["needs_reauthor"]
    with session_scope(engine) as s:
        assert repo.get_source(s, "m1").config == GOOD_CONFIG  # prior config restored


def test_run_reauthor_terminal_verdict_does_not_discard_retained_config(engine, tmp_path):
    """A model that returns a terminal no_timetable verdict during re-author must
    not shelve a retained (flaky-but-correct) config that verify-retry could not
    salvage — the prior config and status are restored."""
    _reauthor_candidate(engine, "m1", tmp_path, config=GOOD_CONFIG)
    harness = FakeHarness(json.dumps({"outcome": "no_timetable", "reason": "looks empty"}))
    outs = run_reauthor(engine, harness=harness, candidate_root=tmp_path,
                        models=("opus@low",), today=date(2026, 6, 1), horizon_days=5,
                        fetcher=_fetcher)
    assert [o.outcome for o in outs] == ["no_timetable"]
    with session_scope(engine) as s:
        src = repo.get_source(s, "m1")
        assert src.config == GOOD_CONFIG          # prior config restored
        assert src.triage_status == "needs_reauthor"  # not shelved as no_timetable


def test_run_reauthor_skips_sources_without_a_bundle(engine, tmp_path):
    """A deterministic-discovery source has no candidate bundle to prompt from;
    model re-author leaves it untouched (no model call, no demotion)."""
    _src(engine, "nb", GOOD_CONFIG)  # no bundle saved on disk
    harness = FakeHarness("garbage")
    outs = run_reauthor(engine, harness=harness, candidate_root=tmp_path,
                        models=("opus@low",), today=date(2026, 6, 1), horizon_days=5,
                        fetcher=_fetcher)
    assert outs == []
    assert harness.calls == []
    with session_scope(engine) as s:
        src = repo.get_source(s, "nb")
        assert src.triage_status == "needs_reauthor"
        assert src.config == GOOD_CONFIG
