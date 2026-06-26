"""Phase 3/5/6 author funnel: deterministic pre-model recovery, the table_mapping
decision path, prompt routing, failure-specific feedback, and attempt history."""
import json
from datetime import date

from directory import repository as repo
from directory.db import session_scope
from directory.ingest.author import (
    author_mosque,
    config_from_table_mapping,
    parse_decision,
    route_prompt_kind,
)
from directory.ingest.discover import Candidate, CandidateBundle
from directory.ingest.evidence import build_page_evidence
from directory.ingest.fetch import FetchResult
from directory.ingest.harness import HarnessResult
from directory.models import Mosque, Occurrence, Source

TODAY = date(2026, 6, 1)

MONTHLY = (
    "<table class='pt'><tr><th>Date</th><th>Fajr</th><th>Dhuhr</th><th>Asr</th>"
    "<th>Maghrib</th><th>Isha</th></tr>"
    "<tr><td>1 June</td><td>05:00</td><td>13:30</td><td>18:30</td><td>21:30</td><td>23:00</td></tr>"
    "<tr><td>2 June</td><td>05:02</td><td>13:31</td><td>18:31</td><td>21:31</td><td>23:01</td></tr>"
    "</table>"
)
# Prayers in the header, two rows of times, NO date column → the enumerator cannot
# author it (ambiguous), but it still routes to a table prompt (prayers named).
DEGENERATE = (
    "<table class='deg'><tr><th>Fajr</th><th>Dhuhr</th><th>Asr</th><th>Maghrib</th>"
    "<th>Isha</th></tr>"
    "<tr><td>05:00</td><td>13:30</td><td>18:30</td><td>21:30</td><td>23:00</td></tr>"
    "<tr><td>05:02</td><td>13:31</td><td>18:31</td><td>21:31</td><td>23:01</td></tr></table>"
)


def _good_config(url, selector="table.pt"):
    return json.dumps({
        "url": url,
        "config": {"shape": "html_table", "grid": {
            "table_selector": selector, "date": {"index": 0}, "columns": [
                {"kind": "jamaah", "prayer": "fajr", "index": 1},
                {"kind": "jamaah", "prayer": "dhuhr", "index": 2},
                {"kind": "jamaah", "prayer": "asr", "index": 3},
                {"kind": "jamaah", "prayer": "maghrib", "index": 4},
                {"kind": "jamaah", "prayer": "isha", "index": 5}]}},
    })


class _RecordingHarness:
    """Returns scripted replies in order (repeating the last), recording prompts."""

    name = "fake"

    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts: list[str] = []
        self.calls: list[str] = []

    def run(self, prompt, *, model):
        self.prompts.append(prompt)
        self.calls.append(model)
        return HarnessResult(self.replies[min(len(self.prompts) - 1, len(self.replies) - 1)],
                             model, True)


def _seed(engine, mid="m1"):
    with session_scope(engine) as s:
        s.add(Mosque(id=mid, name=mid, lat=52.0, lng=-1.0, website_url=f"https://{mid}.example/"))
        s.add(Source(id=mid, mosque_id=mid, url=f"https://{mid}.example/prayer-times",
                     triage_status="candidate"))


def _bundle(mid, html, root, *, with_evidence):
    url = f"https://{mid}.example/prayer-times"
    ev = [build_page_evidence(html, url, today=TODAY)] if with_evidence else []
    CandidateBundle(mid, f"https://{mid}.example/",
                    [Candidate(url, 9.0, html, "Fajr")], evidence=ev).save(root)
    return url


def _fetcher_returning(html):
    def _f(url, **kwargs):
        return FetchResult(url, 200, html, "h", error=None)

    return _f


# ── parse / build table_mapping ───────────────────────────────────────────────


def test_parse_decision_table_mapping():
    raw = json.dumps({"outcome": "table_mapping", "table_id": "table_0",
                      "orientation": "horizontal_multiday", "date_index": 0,
                      "columns": [{"kind": "jamaah", "prayer": "fajr", "index": 1}]})
    d = parse_decision(raw, "u")
    assert d.outcome == "table_mapping"
    assert d.table_id == "table_0"
    assert d.columns == [{"kind": "jamaah", "prayer": "fajr", "index": 1}]


def test_config_from_table_mapping_resolves_selector_from_evidence():
    ev = [build_page_evidence(MONTHLY, "https://m.example/p", today=TODAY)]
    d = parse_decision(json.dumps({
        "outcome": "table_mapping", "table_id": "table_0",
        "orientation": "horizontal_multiday", "date_index": 0,
        "columns": [{"kind": "jamaah", "prayer": "fajr", "index": 1},
                    {"kind": "jamaah", "prayer": "dhuhr", "index": 2}]}), "u")
    config = config_from_table_mapping(d, ev)
    assert config.shape == "html_table"
    assert config.grid.table_selector == "table.pt"  # resolved from evidence by table_id
    assert config.grid.date.index == 0
    assert len(config.grid.columns) == 2


def test_config_from_table_mapping_single_day_omits_date():
    d = parse_decision(json.dumps({
        "outcome": "table_mapping", "orientation": "horizontal_single_day",
        "columns": [{"kind": "jamaah", "prayer": "fajr", "index": 0}]}), "u")
    config = config_from_table_mapping(d, [])
    assert config.grid.single_day is True
    assert config.grid.date is None


# ── prompt routing ────────────────────────────────────────────────────────────


def test_route_prompt_kind():
    monthly = build_page_evidence(MONTHLY, "https://m/p", today=TODAY)
    assert route_prompt_kind([monthly]) == "table_repair"

    two_tables = build_page_evidence(MONTHLY + MONTHLY, "https://m/p", today=TODAY)
    assert route_prompt_kind([two_tables]) == "table_choice"

    pdf = build_page_evidence(
        '<a href="/june-2026-prayer-timetable.pdf">June</a>', "https://m/p", today=TODAY)
    assert route_prompt_kind([pdf]) == "media"

    uc = build_page_evidence(
        "<h1>Site under construction coming soon</h1>", "https://m/p", today=TODAY)
    assert route_prompt_kind([uc]) == "terminal"

    blank = build_page_evidence("<p>hello world this is a page</p>", "https://m/p", today=TODAY)
    assert route_prompt_kind([blank]) == "unknown"


# ── Phase 3: deterministic recovery inside author ─────────────────────────────


def test_evidence_bundle_authors_deterministically_without_model(engine, tmp_path):
    _seed(engine)
    _bundle("m1", MONTHLY, tmp_path, with_evidence=True)
    harness = _RecordingHarness(["should not be called"])

    out = author_mosque(engine, "m1", harness=harness, candidate_root=tmp_path,
                        models=("cheap",), today=TODAY, horizon_days=5,
                        fetcher=_fetcher_returning(MONTHLY))

    assert out.outcome == "authored"
    assert out.model is None          # authored by the enumerator, not a model
    assert harness.calls == []        # no paid model call
    with session_scope(engine) as s:
        assert repo.get_source(s, "m1").triage_status == "authored"
        assert s.query(Occurrence).count() > 0


def test_evidenceless_bundle_still_uses_the_model(engine, tmp_path):
    # An old bundle without evidence skips deterministic recovery (legacy path).
    _seed(engine, "m2")
    url = _bundle("m2", MONTHLY, tmp_path, with_evidence=False)
    harness = _RecordingHarness([_good_config(url)])

    out = author_mosque(engine, "m2", harness=harness, candidate_root=tmp_path,
                        models=("cheap",), today=TODAY, horizon_days=5,
                        fetcher=_fetcher_returning(MONTHLY))

    assert out.outcome == "authored"
    assert harness.calls == ["cheap"]  # model was needed


# ── Phase 5: table_mapping model path ─────────────────────────────────────────


def test_model_table_mapping_authors(engine, tmp_path):
    _seed(engine, "m3")
    url = _bundle("m3", DEGENERATE, tmp_path, with_evidence=False)
    mapping = json.dumps({
        "outcome": "table_mapping", "url": url, "orientation": "horizontal_multiday",
        "date_index": 0, "columns": [
            {"kind": "jamaah", "prayer": "fajr", "index": 1},
            {"kind": "jamaah", "prayer": "dhuhr", "index": 2},
            {"kind": "jamaah", "prayer": "asr", "index": 3},
            {"kind": "jamaah", "prayer": "maghrib", "index": 4},
            {"kind": "jamaah", "prayer": "isha", "index": 5}]})
    harness = _RecordingHarness([mapping])

    out = author_mosque(engine, "m3", harness=harness, candidate_root=tmp_path,
                        models=("cheap",), today=TODAY, horizon_days=5,
                        fetcher=_fetcher_returning(MONTHLY))  # live page has the date column

    assert out.outcome == "authored"
    with session_scope(engine) as s:
        assert s.query(Occurrence).count() > 0


# ── Phase 6: failure-specific feedback + attempt history ──────────────────────


def test_zero_rows_failure_feeds_back_a_table_repair_prompt(engine, tmp_path):
    _seed(engine, "m4")
    # evidence describes the (non-enumerable) degenerate table → routes to table_repair;
    # the live page is the clean monthly table the good config matches.
    url = _bundle("m4", DEGENERATE, tmp_path, with_evidence=True)
    harness = _RecordingHarness([_good_config(url, "table.nope"), _good_config(url, "table.pt")])

    out = author_mosque(engine, "m4", harness=harness, candidate_root=tmp_path,
                        models=("cheap",), today=TODAY, horizon_days=5,
                        fetcher=_fetcher_returning(MONTHLY), feedback_retries=1)

    assert out.outcome == "authored"
    assert len(harness.prompts) == 2
    assert "table_mapping" in harness.prompts[0]          # routed to the table prompt
    assert "PREVIOUS ATTEMPT WAS REJECTED" in harness.prompts[1]
    assert "already tried" in harness.prompts[1]          # failure-specific feedback


def test_attempt_history_is_written(engine, tmp_path):
    _seed(engine, "m5")
    _bundle("m5", MONTHLY, tmp_path, with_evidence=False)
    harness = _RecordingHarness(["not json at all"])
    runs = tmp_path / "runs"

    out = author_mosque(engine, "m5", harness=harness, candidate_root=tmp_path,
                        models=("cheap",), today=TODAY, horizon_days=5,
                        fetcher=_fetcher_returning(MONTHLY), feedback_retries=0, runs_root=runs)

    assert out.outcome == "needs_reauthor"
    data = json.loads((runs / "m5.json").read_text())
    assert data["mosque_id"] == "m5"
    assert data["attempts"][0]["failure_kind"] == "invalid_json"
    assert data["attempts"][0]["prompt_kind"] == "legacy"
