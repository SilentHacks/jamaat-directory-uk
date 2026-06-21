import httpx

from directory import repository as repo
from directory.db import session_scope
from directory.ingest.website import validate_websites
from directory.models import Mosque


def _seed(engine):
    with session_scope(engine) as s:
        s.add_all(
            [
                Mosque(id="ok", name="ok", lat=51.0, lng=-1.0, website_url="https://ok.example/"),
                Mosque(id="moved", name="moved", lat=51.0, lng=-1.0,
                       website_url="https://old.example/"),
                Mosque(id="dead", name="dead", lat=51.0, lng=-1.0,
                       website_url="https://dead.example/"),
                Mosque(id="none", name="none", lat=51.0, lng=-1.0, website_url=None),
            ]
        )


def _handler(request):
    host = request.url.host
    if host == "old.example":
        return httpx.Response(301, headers={"Location": "https://new.example/"})
    if host == "dead.example":
        return httpx.Response(404)
    return httpx.Response(200, text="ok")


def test_validate_repairs_drops_and_keeps(engine):
    _seed(engine)
    client = httpx.Client(transport=httpx.MockTransport(_handler), follow_redirects=True)
    summary = validate_websites(engine, client=client)

    assert summary.checked == 3  # the null-website mosque is skipped
    assert summary.repaired == 1
    assert summary.dropped == 1
    assert summary.unchanged == 1

    with session_scope(engine) as s:
        assert repo.get_mosque(s, "ok").website_url == "https://ok.example/"
        assert repo.get_mosque(s, "moved").website_url == "https://new.example/"
        assert repo.get_mosque(s, "dead").website_url is None
