import json
from datetime import date

import httpx

from directory import repository as repo
from directory.db import session_scope
from directory.ingest.author import author_mosque, parse_decision
from directory.ingest.discover import Candidate, CandidateBundle, discover_mosque
from directory.ingest.fetch import FetchResult
from directory.models import Mosque, Source
from tests.conftest import FakeHarness

TODAY = date(2026, 6, 1)


# ── parse_decision ────────────────────────────────────────────────────────────


def test_parse_decision_terminal_no_timetable():
    d = parse_decision('{"outcome": "no_timetable", "reason": "under construction"}', "u")
    assert d.outcome == "no_timetable"
    assert d.reason == "under construction"
    assert d.config is None


def test_parse_decision_wrong_site():
    d = parse_decision('{"outcome": "wrong_site", "reason": "restaurant"}', "u")
    assert d.outcome == "wrong_site"


def test_parse_decision_media_envelope_builds_config():
    raw = '{"outcome": "media", "kind": "pdf", "url": "https://x/jt.pdf"}'
    d = parse_decision(raw, "u")
    assert d.outcome == "media"
    assert d.config.shape == "pdf"
    assert d.config.media.url == "https://x/jt.pdf"


def test_parse_decision_media_requires_kind_and_url():
    import pytest

    with pytest.raises(ValueError):
        parse_decision('{"outcome": "media", "kind": "pdf"}', "u")


def test_parse_decision_unknown():
    assert parse_decision('{"outcome": "unknown"}', "u").outcome == "unknown"


def test_parse_decision_bare_config_back_compat():
    d = parse_decision('{"shape": "rules", "rules": {"rules": []}}', "u")
    assert d.outcome == "config"
    assert d.config.shape == "rules"


# ── author_mosque terminal handling ───────────────────────────────────────────

TABLE_HTML = (
    "<table class='t'><tr><th>Date</th><th>Fajr</th><th>Dhuhr</th><th>Asr</th>"
    "<th>Maghrib</th><th>Isha</th></tr>"
    "<tr><td>1 June</td><td>05:00</td><td>13:30</td><td>18:30</td><td>21:30</td><td>23:00</td></tr>"
    "</table>"
)


def _candidate_mosque(engine, mid="m1"):
    with session_scope(engine) as s:
        s.add(Mosque(id=mid, name="M1", lat=52.0, lng=-1.0, website_url="https://m1.example/"))
        s.add(
            Source(
                id=mid,
                mosque_id=mid,
                url="https://m1.example/prayer-times",
                triage_status="candidate",
            )
        )


def _bundle(mid="m1"):
    return CandidateBundle(
        mid,
        "https://m1.example/",
        [Candidate("https://m1.example/prayer-times", 9.0, TABLE_HTML, "Fajr 05:00")],
    )


def _fetcher(url, **kwargs):
    return FetchResult(url, 200, TABLE_HTML, "h", error=None)


def test_model_no_timetable_is_terminal_without_escalation(engine, tmp_path):
    _candidate_mosque(engine)
    _bundle().save(tmp_path)
    harness = FakeHarness(json.dumps({"outcome": "no_timetable", "reason": "under construction"}))

    out = author_mosque(
        engine,
        "m1",
        harness=harness,
        candidate_root=tmp_path,
        models=("cheap", "strong"),
        today=TODAY,
        horizon_days=5,
        fetcher=_fetcher,
        feedback_retries=0,
    )

    assert out.outcome == "no_timetable"
    assert harness.calls == ["cheap"]  # terminal: no escalation to strong
    with session_scope(engine) as s:
        src = repo.get_source(s, "m1")
        assert src.triage_status == "no_timetable"
        assert src.last_status == "no_timetable"
        assert src.last_error == "under construction"
        assert src.authored_by == "fake:cheap"


def test_model_wrong_site_records_wrong_site_last_status(engine, tmp_path):
    _candidate_mosque(engine)
    _bundle().save(tmp_path)
    harness = FakeHarness(json.dumps({"outcome": "wrong_site", "reason": "a restaurant"}))

    out = author_mosque(
        engine,
        "m1",
        harness=harness,
        candidate_root=tmp_path,
        models=("cheap",),
        today=TODAY,
        horizon_days=5,
        fetcher=_fetcher,
        feedback_retries=0,
    )

    assert out.outcome == "no_timetable"
    with session_scope(engine) as s:
        src = repo.get_source(s, "m1")
        assert src.triage_status == "no_timetable"
        assert src.last_status == "wrong_site"


def test_model_media_envelope_defers(engine, tmp_path):
    _candidate_mosque(engine)
    _bundle().save(tmp_path)
    harness = FakeHarness(
        json.dumps({"outcome": "media", "kind": "pdf", "url": "https://m1.example/june.pdf"})
    )

    out = author_mosque(
        engine,
        "m1",
        harness=harness,
        candidate_root=tmp_path,
        models=("cheap", "strong"),
        today=TODAY,
        horizon_days=5,
        fetcher=_fetcher,
        feedback_retries=0,
    )

    assert out.outcome == "deferred_media"
    assert harness.calls == ["cheap"]
    with session_scope(engine) as s:
        src = repo.get_source(s, "m1")
        assert src.triage_status == "deferred_media"
        assert src.shape == "pdf"


def test_model_unknown_escalates_then_needs_reauthor(engine, tmp_path):
    _candidate_mosque(engine)
    _bundle().save(tmp_path)
    harness = FakeHarness(json.dumps({"outcome": "unknown"}))

    out = author_mosque(
        engine,
        "m1",
        harness=harness,
        candidate_root=tmp_path,
        models=("cheap", "strong"),
        today=TODAY,
        horizon_days=5,
        fetcher=_fetcher,
        feedback_retries=0,
    )

    assert out.outcome == "needs_reauthor"
    assert harness.calls == ["cheap", "strong"]  # 'unknown' is not terminal → escalates


# ── discovery terminal classification ─────────────────────────────────────────

UNDER_CONSTRUCTION = (
    "<html><head><title>Welcome</title></head><body>"
    "<h1>Site under construction — coming soon.</h1></body></html>"
)
RESTAURANT = (
    "<html><body><h1>Spice Garden Restaurant</h1>"
    "<p>View our menu and book a table. Order online for free delivery.</p></body></html>"
)
HOME_WITH_LINKS = (
    '<html><body><a href="/prayer-times">Prayer Times</a>'
    '<a href="/timetable">Timetable</a></body></html>'
)


def _mosque(engine, mid, url):
    with session_scope(engine) as s:
        s.add(Mosque(id=mid, name=mid, lat=51.0, lng=-1.0, website_url=url))


def _live_client():
    return httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text="ok")),
        follow_redirects=True,
    )


def _fetcher_for(pages):
    def _f(
        url,
        *,
        requires_js=False,
        etag=None,
        last_modified=None,
        client=None,
        renderer=None,
        timeout=20.0,
    ):
        html = pages.get(url)
        if html is None:
            return FetchResult(url, 404, None, None, error="404")
        return FetchResult(url, 200, html, "hash")

    return _f


def test_discovery_under_construction_is_no_timetable(engine, tmp_path):
    _mosque(engine, "uc", "https://uc.example/")
    fetcher = _fetcher_for({"https://uc.example/": UNDER_CONSTRUCTION})

    out = discover_mosque(
        engine,
        "uc",
        fetcher=fetcher,
        client=_live_client(),
        candidate_root=tmp_path,
        today=TODAY,
        horizon_days=20,
    )

    assert out.outcome == "no_timetable"
    assert (tmp_path / "uc.json").exists()  # bundle kept for forced re-authoring
    with session_scope(engine) as s:
        src = repo.get_source(s, "uc")
        assert src.triage_status == "no_timetable"
        assert src.last_status == "under_construction"


def test_discovery_restaurant_is_wrong_site(engine, tmp_path):
    _mosque(engine, "rest", "https://rest.example/")
    fetcher = _fetcher_for({"https://rest.example/": RESTAURANT})

    out = discover_mosque(
        engine,
        "rest",
        fetcher=fetcher,
        client=_live_client(),
        candidate_root=tmp_path,
        today=TODAY,
        horizon_days=20,
    )

    assert out.outcome == "no_timetable"
    with session_scope(engine) as s:
        assert repo.get_source(s, "rest").last_status == "wrong_site"


def test_discovery_keyword_links_still_flow_to_candidate(engine, tmp_path):
    # The linked prayer pages 404 here, so only the link-bearing homepage is usable;
    # it must remain ambiguous (candidate), never terminal.
    _mosque(engine, "amb", "https://amb.example/")
    fetcher = _fetcher_for({"https://amb.example/": HOME_WITH_LINKS})

    out = discover_mosque(
        engine,
        "amb",
        fetcher=fetcher,
        client=_live_client(),
        candidate_root=tmp_path,
        today=TODAY,
        horizon_days=20,
    )

    assert out.outcome == "candidate"
    assert (tmp_path / "amb.json").exists()


def test_discovery_terminal_does_not_overwrite_existing_config(engine, tmp_path):
    # A source that already holds a config is preserved by the anti-clobber guard,
    # so terminal classification never runs against it (no force).
    _mosque(engine, "keep", "https://keep.example/")
    with session_scope(engine) as s:
        s.add(
            Source(
                id="keep",
                mosque_id="keep",
                url="https://keep.example/t",
                shape="html_table",
                platform="generic_table",
                config='{"shape":"html_table"}',
                triage_status="needs_reauthor",
            )
        )
    fetcher = _fetcher_for({"https://keep.example/": UNDER_CONSTRUCTION})

    out = discover_mosque(
        engine,
        "keep",
        fetcher=fetcher,
        client=_live_client(),
        candidate_root=tmp_path,
        today=TODAY,
        horizon_days=20,
    )

    assert out.outcome == "skipped"
    with session_scope(engine) as s:
        src = repo.get_source(s, "keep")
        assert src.config == '{"shape":"html_table"}'
        assert src.triage_status == "needs_reauthor"


def test_discovery_terminal_bundle_carries_evidence(engine, tmp_path):
    # The ambiguous hand-off bundle now ships structured evidence for downstream use.
    _mosque(engine, "amb2", "https://amb2.example/")
    fetcher = _fetcher_for({"https://amb2.example/": HOME_WITH_LINKS})

    discover_mosque(
        engine,
        "amb2",
        fetcher=fetcher,
        client=_live_client(),
        candidate_root=tmp_path,
        today=TODAY,
        horizon_days=20,
    )

    bundle = CandidateBundle.load("amb2", tmp_path)
    assert bundle is not None
    assert len(bundle.evidence) >= 1
    assert bundle.evidence[0].page_class == "unknown"
