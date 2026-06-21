from datetime import date

from directory import repository as repo
from directory.db import session_scope
from directory.ingest.fetch import FetchResult
from directory.ingest.runner import extract_source, run_extract
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
