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


def test_create_or_update_source_inserts_then_updates(engine):
    with session_scope(engine) as s:
        _add(s, "m1", "https://m1.example")
    with session_scope(engine) as s:
        repo.create_or_update_source(
            s,
            source_id="m1",
            mosque_id="m1",
            url="https://m1.example/t",
            platform="wp_prayer",
            shape="html_table",
            config='{"shape":"html_table"}',
            requires_js=False,
            triage_status="authored",
        )
    with session_scope(engine) as s:
        src = repo.get_source(s, "m1")
        assert src.platform == "wp_prayer"
        assert src.triage_status == "authored"
    with session_scope(engine) as s:
        repo.create_or_update_source(
            s,
            source_id="m1",
            mosque_id="m1",
            url="https://m1.example/t2",
            platform="wp_prayer",
            shape="html_table",
            config='{"shape":"html_table"}',
            requires_js=True,
            triage_status="review",
        )
    with session_scope(engine) as s:
        src = repo.get_source(s, "m1")
        assert src.url == "https://m1.example/t2"
        assert src.requires_js == 1
        assert src.triage_status == "review"


def test_mosques_for_discovery_skips_no_website(engine):
    with session_scope(engine) as s:
        _add(s, "has", "https://has.example")
        _add(s, "no", None)
        _add(s, "blank", "")  # upstream uses "" as well as NULL for "no website"
    with session_scope(engine) as s:
        assert [m.id for m in repo.mosques_for_discovery(s)] == ["has"]
