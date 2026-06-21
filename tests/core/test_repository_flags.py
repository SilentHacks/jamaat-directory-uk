from directory import repository as repo
from directory.db import session_scope
from directory.models import Mosque, Source


def _seed(engine):
    with session_scope(engine) as s:
        s.add(Mosque(id="m1", name="M1", lat=52.0, lng=-1.0))
        s.add(Source(id="s1", mosque_id="m1", url="https://m1.example", config="{}",
                     triage_status="authored"))
        s.add(Source(id="s2", mosque_id="m1", url="https://m2.example", config="{}",
                     triage_status="authored"))


def test_set_and_read_flags_round_trip(engine):
    _seed(engine)
    with session_scope(engine) as s:
        repo.set_source_state(s, "s1", flags=["jumuah_missing"])
    with session_scope(engine) as s:
        assert repo.get_source(s, "s1").flags == '["jumuah_missing"]'


def test_sources_with_flag_filters(engine):
    _seed(engine)
    with session_scope(engine) as s:
        repo.set_source_state(s, "s1", flags=["jumuah_missing"])
        repo.set_source_state(s, "s2", flags=[])
    with session_scope(engine) as s:
        flagged = [src.id for src in repo.sources_with_flag(s, "jumuah_missing")]
    assert flagged == ["s1"]


def test_set_flags_clears_previous(engine):
    _seed(engine)
    with session_scope(engine) as s:
        repo.set_source_state(s, "s1", flags=["jumuah_missing"])
    with session_scope(engine) as s:
        repo.set_source_state(s, "s1", flags=[])
    with session_scope(engine) as s:
        assert repo.sources_with_flag(s, "jumuah_missing") == []


def test_set_flags_none_leaves_unchanged(engine):
    _seed(engine)
    with session_scope(engine) as s:
        repo.set_source_state(s, "s1", flags=["jumuah_missing"])
    with session_scope(engine) as s:
        repo.set_source_state(s, "s1", confidence=0.9)  # flags omitted
    with session_scope(engine) as s:
        assert repo.get_source(s, "s1").flags == '["jumuah_missing"]'
