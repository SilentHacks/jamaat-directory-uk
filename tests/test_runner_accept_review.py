from datetime import date

from directory import repository as repo
from directory.db import session_scope
from directory.ingest.fetch import FetchResult
from directory.ingest.runner import extract_source
from directory.models import Mosque, Occurrence, Source

# A fixed-iqamah table with no begin column across >=7 days routes to the
# "review" lane (constant columns, no begin) per Phase-2 gates.
ROWS = "\n".join(
    f"<tr><td>{d} June</td><td>05:00</td><td>13:30</td>"
    f"<td>18:30</td><td>21:30</td><td>23:00</td></tr>"
    for d in range(1, 11)
)
CONSTANT_HTML = (
    "<table class='t'><tr><th>Date</th><th>Fajr</th><th>Dhuhr</th><th>Asr</th>"
    f"<th>Maghrib</th><th>Isha</th></tr>{ROWS}</table>"
)

CONFIG = (
    '{"shape":"html_table","grid":{"table_selector":"table.t","date":{"index":0},'
    '"columns":['
    '{"kind":"jamaah","prayer":"fajr","index":1},'
    '{"kind":"jamaah","prayer":"dhuhr","index":2},'
    '{"kind":"jamaah","prayer":"asr","index":3},'
    '{"kind":"jamaah","prayer":"maghrib","index":4},'
    '{"kind":"jamaah","prayer":"isha","index":5}]}}'
)


def _seed(engine):
    with session_scope(engine) as s:
        s.add(Mosque(id="m1", name="M1", lat=52.0, lng=-1.0))
        s.add(Source(id="m1", mosque_id="m1", url="https://m1.example",
                     config=CONFIG, triage_status="review"))


def _fetcher(html):
    def _f(url, **kwargs):
        return FetchResult(url, 200, html, "h", error=None)
    return _f


def test_default_keeps_constant_table_in_review(engine):
    _seed(engine)
    out = extract_source(engine, "m1", today=date(2026, 6, 1), horizon_days=9,
                         fetcher=_fetcher(CONSTANT_HTML))
    assert out.lane == "review"
    assert out.triage_status == "review"
    with session_scope(engine) as s:
        assert s.query(Occurrence).count() == 0


def test_accept_review_activates_and_writes(engine):
    _seed(engine)
    out = extract_source(engine, "m1", today=date(2026, 6, 1), horizon_days=9,
                         fetcher=_fetcher(CONSTANT_HTML), accept_review=True)
    assert out.triage_status == "authored"
    with session_scope(engine) as s:
        assert s.query(Occurrence).count() > 0
        assert repo.get_source(s, "m1").triage_status == "authored"


def test_accept_review_still_rejects_hard_gate_failure(engine):
    _seed(engine)
    # empty body -> fetch error path -> needs_reauthor regardless of accept_review
    out = extract_source(engine, "m1", today=date(2026, 6, 1), horizon_days=9,
                         fetcher=_fetcher(""), accept_review=True)
    assert out.triage_status == "needs_reauthor"
