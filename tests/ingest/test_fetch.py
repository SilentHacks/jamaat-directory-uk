import sys
import types

import httpx

from directory.ingest.extractors.config_schema import NavSpec
from directory.ingest.fetch import fetch, render_playwright_nav


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


def test_renderer_failure_is_captured_not_raised():
    # A renderer (Playwright) that times out / raises must not crash discovery;
    # it yields an error result so the page is skipped.
    def handler(request):
        return httpx.Response(200, text="<div>spinner</div>")

    def boom(url):
        raise RuntimeError("Page.goto: Timeout 30000ms exceeded")

    res = fetch("https://m.example/spa", requires_js=True, client=_client(handler), renderer=boom)
    assert res.error is not None
    assert "render failed" in res.error
    assert res.html is None


# ---------------------------------------------------------------------------
# render_playwright_nav drives a (faked) browser through calendar months.
# ---------------------------------------------------------------------------
class _FakePage:
    """Records navigation calls; content() returns a fresh per-capture marker so
    each captured month is distinguishable. ``fail_after`` makes the (n+1)th
    navigation step raise, exercising the partial-walk path."""

    def __init__(self, fail_after: int | None = None):
        self.calls: list = []
        self._captures = 0
        self._steps = 0
        self._fail_after = fail_after

    def goto(self, url, **kw):
        self.calls.append(("goto", url))

    def wait_for_load_state(self, *a, **k):
        pass

    def wait_for_selector(self, selector, **k):
        self.calls.append(("ready", selector))

    def wait_for_timeout(self, ms):
        self.calls.append(("settle", ms))

    def _maybe_fail(self):
        self._steps += 1
        if self._fail_after is not None and self._steps > self._fail_after:
            raise RuntimeError("step failed")

    def click(self, selector):
        self._maybe_fail()
        self.calls.append(("click", selector))

    def select_option(self, selector, index=None, label=None):
        self._maybe_fail()
        self.calls.append(("select", selector, index, label))

    def content(self):
        self._captures += 1
        return f"<html>doc{self._captures}</html>"


def _install_fake_playwright(monkeypatch, page):
    mod = types.ModuleType("playwright.sync_api")

    class _TimeoutError(Exception):
        pass

    class _CM:
        def __enter__(self):
            chromium = types.SimpleNamespace(
                launch=lambda **k: types.SimpleNamespace(
                    new_page=lambda: page, close=lambda: None
                )
            )
            return types.SimpleNamespace(chromium=chromium)

        def __exit__(self, *a):
            return False

    mod.TimeoutError = _TimeoutError
    mod.sync_playwright = lambda: _CM()
    monkeypatch.setitem(sys.modules, "playwright", types.ModuleType("playwright"))
    monkeypatch.setitem(sys.modules, "playwright.sync_api", mod)


def test_nav_next_clicks_forward_once_per_later_month(monkeypatch):
    page = _FakePage()
    _install_fake_playwright(monkeypatch, page)
    nav = NavSpec(kind="next", next_selector=".cal-next", ready_selector=".cal-grid")

    htmls = render_playwright_nav("https://js/cal", nav, [(2026, 6), (2026, 7), (2026, 8)])

    assert htmls == ["<html>doc1</html>", "<html>doc2</html>", "<html>doc3</html>"]
    clicks = [c for c in page.calls if c[0] == "click"]
    assert clicks == [("click", ".cal-next"), ("click", ".cal-next")]  # 2 for 3 months
    assert ("ready", ".cal-grid") in page.calls  # awaited, not a fixed settle


def test_nav_select_picks_absolute_month_and_year(monkeypatch):
    page = _FakePage()
    _install_fake_playwright(monkeypatch, page)
    nav = NavSpec(kind="select", month_select="#m", year_select="#y", settle_ms=200)

    htmls = render_playwright_nav("https://js/cal", nav, [(2026, 12), (2027, 1)])

    assert len(htmls) == 2
    selects = [c for c in page.calls if c[0] == "select"]
    # January reached absolutely: month index 0, year label "2027".
    assert ("select", "#m", 0, None) in selects
    assert ("select", "#y", None, "2027") in selects
    assert ("settle", 200) in page.calls  # no ready_selector → fixed settle


def test_nav_partial_walk_returns_what_it_captured(monkeypatch):
    page = _FakePage(fail_after=1)  # second navigation step raises
    _install_fake_playwright(monkeypatch, page)
    nav = NavSpec(kind="next", next_selector=".cal-next")

    htmls = render_playwright_nav("https://js/cal", nav, [(2026, 6), (2026, 7), (2026, 8)])

    assert htmls == ["<html>doc1</html>", "<html>doc2</html>"]  # month0 + one step
