import pytest
from fastapi.testclient import TestClient

from directory.api.app import create_app
from directory.api.deps import get_engine


@pytest.fixture
def client(seeded):
    app = create_app()
    app.dependency_overrides[get_engine] = lambda: seeded
    return TestClient(app)


def test_index_lists_mosques(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Leicester Masjid" in r.text
    assert "London Masjid" in r.text


def test_search_partial_filters(client):
    r = client.get("/search", params={"q": "london"})
    assert r.status_code == 200
    assert "London Masjid" in r.text
    assert "Leicester Masjid" not in r.text


def test_detail_page(client):
    r = client.get("/mosque/lon")
    assert r.status_code == 200
    assert "London Masjid" in r.text
    assert "not recorded" in r.text  # null website


def test_detail_404(client):
    assert client.get("/mosque/nope").status_code == 404


def test_javascript_website_not_rendered_as_link(tmp_path):
    from fastapi.testclient import TestClient

    from directory.api.app import create_app
    from directory.api.deps import get_engine
    from directory.db import init_db, make_engine, session_scope
    from directory.models import Mosque

    engine = make_engine(f"sqlite:///{tmp_path / 't.db'}")
    init_db(engine)
    with session_scope(engine) as s:
        s.add(Mosque(id="x", name="Evil Masjid", lat=1.0, lng=2.0,
                     website_url="javascript:alert(1)"))
    app = create_app()
    app.dependency_overrides[get_engine] = lambda: engine
    client = TestClient(app)
    r = client.get("/mosque/x")
    assert r.status_code == 200
    assert 'href="javascript:' not in r.text
