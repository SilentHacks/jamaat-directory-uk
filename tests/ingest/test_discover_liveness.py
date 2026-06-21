import httpx

from directory.ingest.discover import check_liveness


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def test_200_is_alive():
    res = check_liveness(
        "https://m.example/", client=_client(lambda r: httpx.Response(200, text="ok"))
    )
    assert res.alive is True
    assert res.status == 200
    assert res.final_url == "https://m.example/"
    assert res.error is None


def test_redirect_reports_final_url():
    def handler(request):
        if request.url.host == "old.example":
            return httpx.Response(301, headers={"Location": "https://new.example/"})
        return httpx.Response(200, text="ok")

    res = check_liveness("https://old.example/", client=_client(handler))
    assert res.alive is True
    assert res.final_url == "https://new.example/"


def test_404_is_dead():
    res = check_liveness(
        "https://m.example/", client=_client(lambda r: httpx.Response(404))
    )
    assert res.alive is False
    assert res.status == 404


def test_connect_error_is_dead_with_message():
    def handler(request):
        raise httpx.ConnectError("boom")

    res = check_liveness("https://down.example/", client=_client(handler))
    assert res.alive is False
    assert res.final_url is None
    assert res.error is not None
