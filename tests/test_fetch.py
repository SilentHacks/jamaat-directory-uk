import httpx

from directory.ingest.fetch import fetch


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_200_returns_html_and_hash():
    def handler(request):
        return httpx.Response(200, text="<table>05:00</table>")

    res = fetch("https://m.example/times", client=_client(handler))
    assert res.status == 200
    assert "05:00" in res.html
    assert res.html_hash and len(res.html_hash) == 16
    assert res.not_modified is False


def test_conditional_304_sets_not_modified():
    seen = {}

    def handler(request):
        seen["inm"] = request.headers.get("if-none-match")
        return httpx.Response(304)

    res = fetch("https://m.example/times", etag='"abc"', client=_client(handler))
    assert res.not_modified is True
    assert res.html is None
    assert seen["inm"] == '"abc"'


def test_requires_js_uses_injected_renderer():
    def handler(request):
        return httpx.Response(200, text="<div>spinner</div>")

    res = fetch(
        "https://m.example/spa",
        requires_js=True,
        client=_client(handler),
        renderer=lambda url: "<table>13:30</table>",
    )
    assert "13:30" in res.html


def test_network_error_is_captured():
    def handler(request):
        raise httpx.ConnectError("boom")

    res = fetch("https://down.example", client=_client(handler))
    assert res.error is not None
    assert res.html is None
