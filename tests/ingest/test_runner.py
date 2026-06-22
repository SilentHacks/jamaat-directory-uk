import json
from datetime import date

from directory import repository as repo
from directory.db import session_scope
from directory.ingest.fetch import FetchResult
from directory.ingest.runner import PARTIAL_HORIZON, extract_source, run_extract
from directory.models import Mosque, Occurrence, Source

GOOD_HTML = """
<table class="t">
  <tr><th>Date</th><th>Fajr</th><th>Dhuhr</th><th>Asr</th><th>Maghrib</th><th>Isha</th></tr>
  <tr><td>21 June</td><td>05:00</td><td>13:30</td><td>18:30</td><td>21:30</td><td>23:00</td></tr>
</table>
"""

CONFIG = (
    '{"shape":"html_table","grid":{"table_selector":"table.t","date":{"index":0},'
    '"columns":['
    '{"kind":"jamaah","prayer":"fajr","index":1},'
    '{"kind":"jamaah","prayer":"dhuhr","index":2},'
    '{"kind":"jamaah","prayer":"asr","index":3},'
    '{"kind":"jamaah","prayer":"maghrib","index":4},'
    '{"kind":"jamaah","prayer":"isha","index":5}]}}'
)


def _seed(engine, html_config=CONFIG):
    with session_scope(engine) as s:
        s.add(Mosque(id="m1", name="M1", lat=52.0, lng=-1.0))
        s.add(Source(id="s1", mosque_id="m1", url="https://m1.example",
                     config=html_config, triage_status="authored"))


def _fetcher_returning(html):
    def _f(url, **kwargs):
        return FetchResult(url, 200, html, "h", error=None)
    return _f


def test_clean_source_writes_occurrences_and_activates(engine):
    _seed(engine)
    out = extract_source(engine, "s1", today=date(2026, 6, 20), horizon_days=7,
                         fetcher=_fetcher_returning(GOOD_HTML))
    assert out.lane == "auto_accept"
    assert out.rows_written == 5
    with session_scope(engine) as s:
        assert len(s.query(Occurrence).all()) == 5
        assert repo.get_source(s, "s1").triage_status == "authored"


def test_drift_keeps_last_known_and_flags_reauthor(engine):
    _seed(engine)
    # first good run populates occurrences
    extract_source(engine, "s1", today=date(2026, 6, 20), horizon_days=7,
                   fetcher=_fetcher_returning(GOOD_HTML))
    # now the site returns an empty page → 0 rows
    out = extract_source(engine, "s1", today=date(2026, 6, 20), horizon_days=7,
                         fetcher=_fetcher_returning("<p>down for maintenance</p>"))
    assert out.triage_status == "needs_reauthor"
    with session_scope(engine) as s:
        # last-known occurrences preserved, not overwritten with emptiness
        assert len(s.query(Occurrence).all()) == 5
        assert repo.get_source(s, "s1").last_error


def test_fetch_error_flags_reauthor_without_wiping(engine):
    _seed(engine)
    extract_source(engine, "s1", today=date(2026, 6, 20), horizon_days=7,
                   fetcher=_fetcher_returning(GOOD_HTML))

    def _broken(url, **kwargs):
        return FetchResult(url, 0, None, None, error="ConnectError: boom")

    out = extract_source(engine, "s1", today=date(2026, 6, 20), horizon_days=7, fetcher=_broken)
    assert out.ok is False
    assert out.triage_status == "needs_reauthor"
    with session_scope(engine) as s:
        assert len(s.query(Occurrence).all()) == 5


def test_run_extract_processes_all_authored(engine):
    _seed(engine)
    outs = run_extract(engine, today=date(2026, 6, 20), horizon_days=7,
                       fetcher=_fetcher_returning(GOOD_HTML))
    assert [o.source_id for o in outs] == ["s1"]


# ---------------------------------------------------------------------------
# Month paging: a source whose timetable spans monthly pages.
# ---------------------------------------------------------------------------
def _month_row(day: str) -> str:
    # One day-only row with all five prayers; the resolved date depends on the
    # (year, month) the page is extracted under.
    return (
        '<table class="t">'
        "<tr><th>Date</th><th>Fajr</th><th>Dhuhr</th><th>Asr</th>"
        "<th>Maghrib</th><th>Isha</th></tr>"
        f"<tr><td>{day}</td><td>05:00</td><td>13:30</td><td>18:30</td>"
        "<td>21:30</td><td>23:00</td></tr></table>"
    )


PAGED_CONFIG = (
    '{"shape":"html_table","grid":{"table_selector":"table.t",'
    '"date":{"index":0,"format":"day_only"},"columns":['
    '{"kind":"jamaah","prayer":"fajr","index":1},'
    '{"kind":"jamaah","prayer":"dhuhr","index":2},'
    '{"kind":"jamaah","prayer":"asr","index":3},'
    '{"kind":"jamaah","prayer":"maghrib","index":4},'
    '{"kind":"jamaah","prayer":"isha","index":5}]},'
    '"paging":{"mode":"url_template","url_template":"https://m1.example/{year}/{month:02d}"}}'
)


def _paged_fetcher(pages):
    def _f(url, **kwargs):
        if url in pages:
            return FetchResult(url, 200, pages[url], "h")
        return FetchResult(url, 404, None, None, error="not found")

    return _f


def _source_flags(engine, source_id):
    with session_scope(engine) as s:
        return json.loads(repo.get_source(s, source_id).flags or "[]")


def test_url_template_writes_occurrences_across_months(engine):
    _seed(engine, PAGED_CONFIG)
    fetcher = _paged_fetcher({
        "https://m1.example/2026/06": _month_row("25"),  # in horizon (>= Jun 20)
        "https://m1.example/2026/07": _month_row("05"),  # in horizon (<= Jul 20)
    })
    out = extract_source(engine, "s1", today=date(2026, 6, 20), horizon_days=30,
                         fetcher=fetcher)
    assert out.lane == "auto_accept"
    assert out.rows_written == 10  # five prayers on each of two dates
    with session_scope(engine) as s:
        dates = sorted({o.date for o in s.query(Occurrence).all()})
        assert dates == ["2026-06-25", "2026-07-05"]
    assert PARTIAL_HORIZON not in _source_flags(engine, "s1")


def test_url_template_current_month_failure_reauthors(engine):
    _seed(engine, PAGED_CONFIG)
    # current month (June) missing → fatal, even though July is available
    fetcher = _paged_fetcher({"https://m1.example/2026/07": _month_row("05")})
    out = extract_source(engine, "s1", today=date(2026, 6, 20), horizon_days=30,
                         fetcher=fetcher)
    assert out.triage_status == "needs_reauthor"
    with session_scope(engine) as s:
        assert s.query(Occurrence).all() == []


def test_url_template_missing_future_month_is_partial_not_fatal(engine):
    _seed(engine, PAGED_CONFIG)
    # July not yet published → keep June, flag partial, stay authored
    fetcher = _paged_fetcher({"https://m1.example/2026/06": _month_row("25")})
    out = extract_source(engine, "s1", today=date(2026, 6, 20), horizon_days=30,
                         fetcher=fetcher)
    assert out.triage_status == "authored"
    assert out.rows_written == 5
    assert PARTIAL_HORIZON in _source_flags(engine, "s1")


RENDER_NAV_CONFIG = (
    '{"shape":"html_table","grid":{"table_selector":"table.t",'
    '"date":{"index":0,"format":"day_only"},"columns":['
    '{"kind":"jamaah","prayer":"fajr","index":1},'
    '{"kind":"jamaah","prayer":"dhuhr","index":2},'
    '{"kind":"jamaah","prayer":"asr","index":3},'
    '{"kind":"jamaah","prayer":"maghrib","index":4},'
    '{"kind":"jamaah","prayer":"isha","index":5}]},'
    '"paging":{"mode":"render_nav","nav":{"kind":"next","next_selector":".n"}}}'
)


def test_render_nav_source_uses_nav_renderer(engine):
    with session_scope(engine) as s:
        s.add(Mosque(id="m1", name="M1", lat=52.0, lng=-1.0))
        s.add(Source(id="s1", mosque_id="m1", url="https://m1.example/cal",
                     config=RENDER_NAV_CONFIG, triage_status="authored", requires_js=1))

    def nav_renderer(url, nav, months):
        # current month shows day 25, next month day 5
        return [_month_row("25"), _month_row("05")]

    out = extract_source(engine, "s1", today=date(2026, 6, 20), horizon_days=30,
                         fetcher=_paged_fetcher({}), nav_renderer=nav_renderer)
    assert out.lane == "auto_accept"
    with session_scope(engine) as s:
        dates = sorted({o.date for o in s.query(Occurrence).all()})
        assert dates == ["2026-06-25", "2026-07-05"]
