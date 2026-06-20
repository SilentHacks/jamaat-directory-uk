from directory.db import init_db, make_engine, session_scope
from directory.models import Mosque, Occurrence


def test_insert_and_query_mosque(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path/'t.db'}")
    init_db(engine)
    with session_scope(engine) as s:
        s.add(Mosque(id="m1", name="Masjid A", lat=1.0, lng=2.0, aliases='["X"]'))
    with session_scope(engine) as s:
        m = s.get(Mosque, "m1")
        assert m.name == "Masjid A"
        assert m.aliases_list == ["X"]
        assert m.country == "GB"
        assert m.status == "active"


def test_occurrence_round_trips(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path/'t.db'}")
    init_db(engine)
    with session_scope(engine) as s:
        s.add(Mosque(id="m1", name="A", lat=1.0, lng=2.0))
    with session_scope(engine) as s:
        s.add(
            Occurrence(
                mosque_id="m1",
                date="2026-06-21",
                prayer="fajr",
                session_idx=0,
                jamaah_time="05:00",
            )
        )
    with session_scope(engine) as s:
        occ = s.query(Occurrence).one()
        assert occ.jamaah_time == "05:00"
        assert occ.session_idx == 0
