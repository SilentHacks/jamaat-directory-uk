from directory import repository as repo
from directory.db import session_scope
from directory.ingest.materialize import OccurrenceRow
from directory.models import Mosque, Occurrence, Source


def _seed_source(engine):
    with session_scope(engine) as s:
        s.add(Mosque(id="m1", name="M1", lat=52.0, lng=-1.0))
        s.add(Source(id="s1", mosque_id="m1", url="https://m1.example", config="{}",
                     triage_status="authored"))


def test_authored_sources_filters(engine):
    _seed_source(engine)
    with session_scope(engine) as s:
        s.add(Source(id="s2", mosque_id="m1", url=None, config=None, triage_status="candidate"))
    with session_scope(engine) as s:
        ids = [src.id for src in repo.authored_sources(s)]
    assert ids == ["s1"]


def test_replace_source_occurrences_is_idempotent(engine):
    _seed_source(engine)
    rows = [OccurrenceRow("2026-06-21", "fajr", 0, "05:00", "04:45", None)]
    with session_scope(engine) as s:
        n = repo.replace_source_occurrences(s, "s1", "m1", rows)
    assert n == 1
    # replace again with a different set → old rows gone, new rows present
    rows2 = [OccurrenceRow("2026-06-21", "dhuhr", 0, "13:30", None, None)]
    with session_scope(engine) as s:
        repo.replace_source_occurrences(s, "s1", "m1", rows2)
    with session_scope(engine) as s:
        got = s.query(Occurrence).all()
        count = len(got)
        prayer = got[0].prayer
    assert count == 1
    assert prayer == "dhuhr"


def test_record_run_and_set_state(engine):
    _seed_source(engine)
    with session_scope(engine) as s:
        repo.record_extractor_run(s, "s1", ok=True, rows_written=5)
        repo.set_source_state(s, "s1", triage_status="needs_reauthor", last_error="drift")
    with session_scope(engine) as s:
        src = repo.get_source(s, "s1")
        status = src.triage_status
        error = src.last_error
    assert status == "needs_reauthor"
    assert error == "drift"
