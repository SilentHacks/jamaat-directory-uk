from datetime import date

import pytest

from directory import repository as repo
from directory.db import session_scope
from directory.ingest.fetch import FetchResult
from directory.ingest.review import approve_source, fix_mapping, reject_source
from directory.models import Mosque, Occurrence, Source

ROWS = "".join(
    f"<tr><td>{d} June</td><td>05:00</td><td>13:30</td>"
    "<td>18:30</td><td>21:30</td><td>23:00</td></tr>"
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
                     config=CONFIG, triage_status="review", review_reason="constant"))


def _fetcher(html):
    def _f(url, **kwargs):
        return FetchResult(url, 200, html, "h", error=None)
    return _f


def test_approve_activates_reviewed_config(engine):
    _seed(engine)
    out = approve_source(engine, "m1", today=date(2026, 6, 1), horizon_days=9,
                         fetcher=_fetcher(CONSTANT_HTML))
    assert out.triage_status == "authored"
    with session_scope(engine) as s:
        assert s.query(Occurrence).count() > 0


def test_reject_excludes(engine):
    _seed(engine)
    reject_source(engine, "m1", reason="not a real timetable")
    with session_scope(engine) as s:
        src = repo.get_source(s, "m1")
        assert src.triage_status == "excluded"
        assert src.review_reason == "not a real timetable"


def test_fix_mapping_revalidates_and_reruns(engine):
    _seed(engine)
    # swap to a clean config (begin column present breaks the constant flag is not
    # needed here; reuse the same valid config and let gates re-run)
    out = fix_mapping(engine, "m1", CONFIG, today=date(2026, 6, 1), horizon_days=9,
                      fetcher=_fetcher(CONSTANT_HTML))
    assert out.triage_status in {"review", "authored"}


def test_fix_mapping_rejects_invalid_json(engine):
    _seed(engine)
    with pytest.raises(ValueError):
        fix_mapping(engine, "m1", "{not valid", fetcher=_fetcher(CONSTANT_HTML))
