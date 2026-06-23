"""image/pdf sources: the daily timetable lives in an image or PDF the engine
cannot map, so extract_source short-circuits to a terminal deferred_media state —
capturing any structured Jumu'ah, recording the media URL, never fetching the
page (the media itself is parsed in a later phase)."""
from datetime import date

from directory import repository as repo
from directory.db import session_scope
from directory.ingest.fetch import FetchResult
from directory.ingest.runner import DEFERRED_MEDIA, extract_source
from directory.models import Mosque, Occurrence, Source

IMAGE_WITH_JUMUAH = (
    '{"shape":"image","media":{"url":"https://m1.example/june.jpg"},'
    '"jumuah":{"source":"fixed","sessions":['
    '{"label":"Khutbah","time":"13:20"},{"label":"Salah","time":"13:40"}]}}'
)
IMAGE_NO_JUMUAH = '{"shape":"image","media":{"url":"https://m1.example/june.jpg"}}'
PDF_WITH_JUMUAH = (
    '{"shape":"pdf","media":{"url":"https://m1.example/timetable.pdf"},'
    '"jumuah":{"source":"fixed","sessions":[{"label":"Jumu\\u2019ah","time":"13:30"}]}}'
)
IMAGE_BAD_JUMUAH = (
    '{"shape":"image","media":{"url":"https://m1.example/june.jpg"},'
    '"jumuah":{"source":"fixed","sessions":[{"label":"Jumu\\u2019ah","time":"09:00"}]}}'
)


def _seed(engine, config):
    with session_scope(engine) as s:
        s.add(Mosque(id="m1", name="M1", lat=52.0, lng=-1.0))
        s.add(Source(id="s1", mosque_id="m1", url="https://m1.example",
                     config=config, triage_status="authored"))


class _Spy:
    def __init__(self):
        self.calls = 0

    def __call__(self, url, **kwargs):
        self.calls += 1
        return FetchResult(url, 200, "<html></html>", "h", error=None)


def test_image_with_jumuah_defers_and_captures_jumuah_without_fetching(engine):
    _seed(engine, IMAGE_WITH_JUMUAH)
    spy = _Spy()
    out = extract_source(engine, "s1", today=date(2026, 6, 20), horizon_days=14, fetcher=spy)

    assert out.triage_status == DEFERRED_MEDIA
    assert out.ok is True
    assert spy.calls == 0  # the image is not fetched/parsed here
    with session_scope(engine) as s:
        occ = s.query(Occurrence).all()
        # only Jumu'ah captured (two Fridays in the 14-day horizon, 2 sessions each)
        assert occ and all(o.prayer == "jumuah" for o in occ)
        assert {o.jamaah_time for o in occ} == {"13:20", "13:40"}
        assert repo.get_source(s, "s1").triage_status == DEFERRED_MEDIA


def test_image_without_jumuah_defers_with_zero_rows(engine):
    _seed(engine, IMAGE_NO_JUMUAH)
    spy = _Spy()
    out = extract_source(engine, "s1", today=date(2026, 6, 20), horizon_days=14, fetcher=spy)

    assert out.triage_status == DEFERRED_MEDIA
    assert out.rows_written == 0
    assert spy.calls == 0
    with session_scope(engine) as s:
        assert s.query(Occurrence).all() == []
        assert repo.get_source(s, "s1").triage_status == DEFERRED_MEDIA


def test_pdf_shape_defers_like_image(engine):
    _seed(engine, PDF_WITH_JUMUAH)
    out = extract_source(engine, "s1", today=date(2026, 6, 20), horizon_days=14, fetcher=_Spy())
    assert out.triage_status == DEFERRED_MEDIA
    with session_scope(engine) as s:
        assert {o.jamaah_time for o in s.query(Occurrence).all()} == {"13:30"}


def test_implausible_jumuah_reauthors_not_defers(engine):
    _seed(engine, IMAGE_BAD_JUMUAH)  # 09:00 is outside the jumuah window
    out = extract_source(engine, "s1", today=date(2026, 6, 20), horizon_days=14, fetcher=_Spy())
    assert out.triage_status == "needs_reauthor"
    with session_scope(engine) as s:
        assert s.query(Occurrence).all() == []
        assert repo.get_source(s, "s1").last_error
