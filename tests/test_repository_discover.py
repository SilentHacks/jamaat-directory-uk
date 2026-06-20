from directory import repository as repo
from directory.db import session_scope
from directory.models import Mosque


def _add(session, mid, url):
    session.add(Mosque(id=mid, name=mid, lat=51.0, lng=-1.0, website_url=url))


def test_mosques_with_website_filters_nulls(engine):
    with session_scope(engine) as s:
        _add(s, "a", "https://a.example")
        _add(s, "b", None)
    with session_scope(engine) as s:
        ids = [m.id for m in repo.mosques_with_website(s)]
    assert ids == ["a"]


def test_update_mosque_website_can_null(engine):
    with session_scope(engine) as s:
        _add(s, "a", "https://a.example")
    with session_scope(engine) as s:
        repo.update_mosque_website(s, "a", None)
    with session_scope(engine) as s:
        assert repo.get_mosque(s, "a").website_url is None
