from directory import repository as repo
from directory.db import session_scope


def test_upsert_mosques_inserts_and_replaces(engine):
    with session_scope(engine) as s:
        n = repo.upsert_mosques(s, [
            {"id": "m1", "name": "A", "lat": 1.0, "lng": 2.0},
        ])
        assert n == 1
    with session_scope(engine) as s:
        repo.upsert_mosques(s, [{"id": "m1", "name": "A renamed", "lat": 1.0, "lng": 2.0}])
    with session_scope(engine) as s:
        m = repo.get_mosque(s, "m1")
        assert m.name == "A renamed"


def test_list_filters_by_city(seeded):
    with session_scope(seeded) as s:
        rows = repo.list_mosques(s, city="London")
        assert [m.id for m in rows] == ["lon"]


def test_list_has_times_filter(seeded):
    with session_scope(seeded) as s:
        with_times = repo.list_mosques(s, has_times=True)
        without = repo.list_mosques(s, has_times=False)
        assert [m.id for m in with_times] == ["leic"]
        assert [m.id for m in without] == ["lon"]


def test_bbox_filter(seeded):
    with session_scope(seeded) as s:
        # bbox around Leicester only
        rows = repo.list_mosques(s, bbox=(-1.3, 52.5, -0.9, 52.8))
        assert [m.id for m in rows] == ["leic"]


def test_near_radius_filter(seeded):
    with session_scope(seeded) as s:
        rows = repo.list_mosques(s, near=(51.51, -0.13), radius_km=20)
        assert [m.id for m in rows] == ["lon"]


def test_get_times_range(seeded):
    with session_scope(seeded) as s:
        occ = repo.get_times(s, "leic", "2026-06-21", "2026-06-21")
        prayers = sorted({o.prayer for o in occ})
        assert prayers == ["fajr", "jumuah"]
        assert len([o for o in occ if o.prayer == "jumuah"]) == 2


def test_query_times_by_prayer(seeded):
    with session_scope(seeded) as s:
        rows = repo.query_times(s, date="2026-06-21", prayer="fajr")
        assert len(rows) == 1
        mosque, occ = rows[0]
        assert mosque.id == "leic"
        assert occ.jamaah_time == "05:00"
