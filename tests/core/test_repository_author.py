from directory import repository as repo
from directory.db import session_scope
from directory.models import Mosque, Source


def _seed(engine, status, sid):
    with session_scope(engine) as s:
        s.add(Mosque(id=sid, name=sid, lat=52.0, lng=-1.0))
        s.add(Source(id=sid, mosque_id=sid, triage_status=status))


def test_candidate_sources_only_returns_candidates(engine):
    _seed(engine, "candidate", "c1")
    _seed(engine, "authored", "a1")
    _seed(engine, "review", "r1")
    with session_scope(engine) as s:
        ids = [x.id for x in repo.candidate_sources(s)]
    assert ids == ["c1"]


def test_sources_in_review_only_returns_review(engine):
    _seed(engine, "candidate", "c1")
    _seed(engine, "review", "r1")
    _seed(engine, "review", "r2")
    with session_scope(engine) as s:
        ids = [x.id for x in repo.sources_in_review(s)]
    assert ids == ["r1", "r2"]


def test_set_source_state_stamps_authorship(engine):
    _seed(engine, "candidate", "c1")
    with session_scope(engine) as s:
        repo.set_source_state(
            s, "c1", authored_by="opencode:cheap", authored_at="2026-06-20T00:00:00"
        )
    with session_scope(engine) as s:
        src = repo.get_source(s, "c1")
        assert src.authored_by == "opencode:cheap"
        assert src.authored_at == "2026-06-20T00:00:00"
