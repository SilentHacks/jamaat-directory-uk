from datetime import date

from directory import repository as repo
from directory.db import session_scope
from directory.ingest.fetch import FetchResult
from directory.ingest.runner import extract_source
from directory.models import Mosque, Source
from tests.conftest import FIXTURES

FIXTURE = FIXTURES / "timetable_sample.html"

CONFIG = (
    '{"shape":"html_table","grid":{"table_selector":"table.prayer-times","date":{"index":0},'
    '"columns":['
    '{"kind":"begin","prayer":"fajr","index":1},'
    '{"kind":"jamaah","prayer":"fajr","index":2},'
    '{"kind":"jamaah","prayer":"dhuhr","index":3},'
    '{"kind":"jamaah","prayer":"asr","index":4},'
    '{"kind":"jamaah","prayer":"maghrib","index":5},'
    '{"kind":"jamaah","prayer":"isha","index":6}]},'
    '"jumuah":{"source":"fixed","sessions":['
    '{"label":"1st Jumu’ah","time":"13:15"},'
    '{"label":"2nd Jumu’ah","time":"14:00"}]}}'
)


def test_full_pipeline_writes_api_shaped_occurrences(engine):
    html = FIXTURE.read_text()
    with session_scope(engine) as s:
        s.add(Mosque(id="m1", name="M1", lat=52.0, lng=-1.0))
        s.add(Source(id="s1", mosque_id="m1", url="https://m1.example",
                     config=CONFIG, triage_status="authored"))

    def fetcher(url, **kwargs):
        return FetchResult(url, 200, html, "h")

    out = extract_source(engine, "s1", today=date(2026, 6, 19), horizon_days=14, fetcher=fetcher)
    assert out.lane == "auto_accept"

    with session_scope(engine) as s:
        # daily prayer on 2026-06-20, with begin captured
        day = repo.get_times(s, "m1", "2026-06-20", "2026-06-20")
        fajr = [o for o in day if o.prayer == "fajr"][0]
        assert fajr.jamaah_time == "05:00"
        assert fajr.begin_time == "02:50"
        # Jumu'ah materialised onto Friday 2026-06-19 and 2026-06-26
        fri = repo.get_times(s, "m1", "2026-06-19", "2026-06-19")
        jum = sorted((o for o in fri if o.prayer == "jumuah"), key=lambda o: o.session_idx)
        assert [(o.session_idx, o.jamaah_time, o.label) for o in jum] == [
            (1, "13:15", "1st Jumu’ah"),
            (2, "14:00", "2nd Jumu’ah"),
        ]


PAGED_CONFIG = (
    '{"shape":"html_table","grid":{"table_selector":"table.month-times",'
    '"date":{"index":0,"format":"day_only"},"columns":['
    '{"kind":"jamaah","prayer":"fajr","index":1},'
    '{"kind":"jamaah","prayer":"dhuhr","index":2},'
    '{"kind":"jamaah","prayer":"asr","index":3},'
    '{"kind":"jamaah","prayer":"maghrib","index":4},'
    '{"kind":"jamaah","prayer":"isha","index":5}]},'
    '"paging":{"mode":"url_template","url_template":"https://m1.example/{year}/{month:02d}"}}'
)


def test_url_template_pipeline_crawls_two_months(engine):
    month_a = (FIXTURES / "paging_month_a.html").read_text()
    month_b = (FIXTURES / "paging_month_b.html").read_text()
    pages = {
        "https://m1.example/2026/06": month_a,
        "https://m1.example/2026/07": month_b,
    }
    with session_scope(engine) as s:
        s.add(Mosque(id="m1", name="M1", lat=52.0, lng=-1.0))
        s.add(Source(id="s1", mosque_id="m1", url="https://m1.example/cal",
                     config=PAGED_CONFIG, triage_status="authored"))

    def fetcher(url, **kwargs):
        if url in pages:
            return FetchResult(url, 200, pages[url], "h")
        return FetchResult(url, 404, None, None, error="not found")

    out = extract_source(engine, "s1", today=date(2026, 6, 19), horizon_days=30, fetcher=fetcher)
    assert out.lane == "auto_accept"

    with session_scope(engine) as s:
        # A day from the current month's page, resolved under June.
        jun = repo.get_times(s, "m1", "2026-06-25", "2026-06-25")
        assert [o.jamaah_time for o in jun if o.prayer == "fajr"] == ["04:32"]
        # A day from the next month's page, resolved under July — only reachable
        # because the second monthly page was crawled.
        jul = repo.get_times(s, "m1", "2026-07-05", "2026-07-05")
        assert [o.jamaah_time for o in jul if o.prayer == "fajr"] == ["04:40"]
        assert {o.prayer for o in jul} == {"fajr", "dhuhr", "asr", "maghrib", "isha"}
