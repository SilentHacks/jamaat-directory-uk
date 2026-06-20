from datetime import date
from pathlib import Path

from directory import repository as repo
from directory.db import session_scope
from directory.ingest.fetch import FetchResult
from directory.ingest.runner import extract_source
from directory.models import Mosque, Source

FIXTURE = Path(__file__).parent / "fixtures" / "timetable_sample.html"

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
