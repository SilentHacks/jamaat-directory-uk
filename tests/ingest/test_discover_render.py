from datetime import date

import httpx

from directory import repository as repo
from directory.db import session_scope
from directory.ingest.discover import _page_needs_render, discover_mosque
from directory.ingest.fetch import FetchResult
from directory.models import Mosque

HOME_WITH_LINKS = (
    '<html><body><a href="/prayer-times">Prayer Times</a>'
    '<a href="/timetable">Timetable</a></body></html>'
)

# A JS shell: prayer-headed table with an empty tbody — no data statically.
SHELL_TABLE = """
<html><body><h1>Prayer Times</h1>
<table><thead><tr><th>Date</th><th>Fajr</th><th>Dhuhr</th><th>Asr</th>
<th>Maghrib</th><th>Isha</th></tr></thead><tbody></tbody></table>
</body></html>
"""

# The same page once JavaScript has hydrated the rows.
RENDERED_TABLE = """
<html><body><table>
  <tr><th>Date</th><th>Fajr</th><th>Dhuhr</th><th>Asr</th><th>Maghrib</th><th>Isha</th></tr>
  <tr><td>1 June</td><td>05:00</td><td>13:15</td><td>18:30</td><td>21:10</td><td>22:30</td></tr>
  <tr><td>2 June</td><td>05:02</td><td>13:16</td><td>18:31</td><td>21:11</td><td>22:31</td></tr>
  <tr><td>3 June</td><td>05:03</td><td>13:17</td><td>18:32</td><td>21:12</td><td>22:32</td></tr>
</table></body></html>
"""

WP_HTML = """
<html><body>
<table class="dpt_table">
  <tr><th>Date</th><th>Fajr</th><th>Dhuhr</th><th>Asr</th><th>Maghrib</th><th>Isha</th></tr>
  <tr><td>1</td><td>05:00</td><td>13:15</td><td>18:30</td><td>21:10</td><td>22:30</td></tr>
  <tr><td>2</td><td>05:02</td><td>13:16</td><td>18:31</td><td>21:11</td><td>22:31</td></tr>
</table></body></html>
"""


def _mosque(engine, mid, url):
    with session_scope(engine) as s:
        s.add(Mosque(id=mid, name=mid, lat=51.0, lng=-1.0, website_url=url))


def _live_client():
    return httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text="ok")),
        follow_redirects=True,
    )


# ---------------------------------------------------------------------------
# Fix 1: HTTP error pages (4xx/5xx) must never feed the detector.
# ---------------------------------------------------------------------------
def test_404_body_with_table_is_not_authored(engine, tmp_path):
    """A 404 page that happens to carry a valid prayer table must be skipped,
    not authored from."""
    _mosque(engine, "soft404", "https://soft404.example/")

    def fetcher(url, *, requires_js=False, etag=None, last_modified=None,
                client=None, renderer=None, timeout=20.0):
        if url == "https://soft404.example/":
            return FetchResult(url, 200, HOME_WITH_LINKS, "h")
        if url == "https://soft404.example/prayer-times":
            # 404 status, but a real WP table in the body
            return FetchResult(url, 404, WP_HTML, "h")
        return FetchResult(url, 404, "<html>not found</html>", "h")

    out = discover_mosque(engine, "soft404", fetcher=fetcher, client=_live_client(),
                          candidate_root=tmp_path, today=date(2026, 6, 1), horizon_days=20)
    assert out.outcome == "candidate"
    assert out.platform is None


# ---------------------------------------------------------------------------
# Fix 2: JS-rendered sites are escalated after a static miss.
# ---------------------------------------------------------------------------
def _js_fetcher(static_pages, rendered_pages):
    def fetcher(url, *, requires_js=False, etag=None, last_modified=None,
                client=None, renderer=None, timeout=20.0):
        if requires_js and renderer is not None and url in rendered_pages:
            return FetchResult(url, 200, rendered_pages[url], "h")
        if url in static_pages:
            return FetchResult(url, 200, static_pages[url], "h")
        return FetchResult(url, 404, "<html>not found</html>", "h")

    return fetcher


def test_js_shell_escalates_and_authors(engine, tmp_path):
    _mosque(engine, "spa", "https://spa.example/")
    fetcher = _js_fetcher(
        static_pages={
            "https://spa.example/": HOME_WITH_LINKS,
            "https://spa.example/prayer-times": SHELL_TABLE,
        },
        rendered_pages={"https://spa.example/prayer-times": RENDERED_TABLE},
    )
    out = discover_mosque(engine, "spa", fetcher=fetcher, client=_live_client(),
                          candidate_root=tmp_path, today=date(2026, 6, 1), horizon_days=20,
                          renderer=lambda u: RENDERED_TABLE)
    assert out.outcome == "authored"
    assert out.platform == "generic_table"
    with session_scope(engine) as s:
        src = repo.get_source(s, "spa")
        assert src.triage_status == "authored"
        assert bool(src.requires_js) is True


# A rendered DOM that carries the real prayer times but in a shape the
# deterministic enumerator cannot auto-author (no parseable date axis), so discovery
# falls through to the AI candidate bundle. A1 requires the bundle to carry THIS
# rendered HTML, not the empty pre-hydration shell.
RENDERED_UNVERIFIABLE = """
<html><body><table>
  <tr><th>When</th><th>Fajr</th><th>Dhuhr</th><th>Asr</th><th>Maghrib</th><th>Isha</th></tr>
  <tr><td>today</td><td>05:00</td><td>13:15</td><td>18:30</td><td>21:10</td><td>22:30</td></tr>
  <tr><td>tomorrow</td><td>05:02</td><td>13:16</td><td>18:31</td><td>21:11</td><td>22:31</td></tr>
</table></body></html>
"""


def test_unverifiable_js_render_feeds_rendered_dom_to_bundle(engine, tmp_path):
    """A1: when a JS page renders but cannot be auto-authored, the candidate bundle
    handed to the model must contain the rendered DOM (with the real times) and flag
    the page requires_js — never the empty static shell the model cannot read."""
    from directory.ingest.discover import CandidateBundle

    _mosque(engine, "spa3", "https://spa3.example/")
    fetcher = _js_fetcher(
        static_pages={
            "https://spa3.example/": HOME_WITH_LINKS,
            "https://spa3.example/prayer-times": SHELL_TABLE,  # empty tbody
        },
        rendered_pages={"https://spa3.example/prayer-times": RENDERED_UNVERIFIABLE},
    )
    out = discover_mosque(engine, "spa3", fetcher=fetcher, client=_live_client(),
                          candidate_root=tmp_path, today=date(2026, 6, 1), horizon_days=20,
                          renderer=lambda u: RENDERED_UNVERIFIABLE)
    assert out.outcome == "candidate"

    bundle = CandidateBundle.load("spa3", tmp_path)
    pt = next(c for c in bundle.candidates if c.url.endswith("/prayer-times"))
    assert "05:00" in pt.region_html  # rendered-only time reached the model
    assert pt.requires_js is True
    # Evidence is built from the rendered DOM too, so the table is visible to the
    # type-specific prompt router (not lost as an empty shell).
    ev = next(e for e in bundle.evidence if e.url.endswith("/prayer-times"))
    assert any(t.time_count > 0 for t in ev.tables)
    with session_scope(engine) as s:
        assert bool(repo.get_source(s, "spa3").requires_js) is True


def test_no_renderer_means_no_escalation(engine, tmp_path):
    """Without a renderer the JS shell falls through to candidate (legacy behaviour)."""
    _mosque(engine, "spa2", "https://spa2.example/")
    fetcher = _js_fetcher(
        static_pages={
            "https://spa2.example/": HOME_WITH_LINKS,
            "https://spa2.example/prayer-times": SHELL_TABLE,
        },
        rendered_pages={"https://spa2.example/prayer-times": RENDERED_TABLE},
    )
    out = discover_mosque(engine, "spa2", fetcher=fetcher, client=_live_client(),
                          candidate_root=tmp_path, today=date(2026, 6, 1), horizon_days=20)
    assert out.outcome == "candidate"


def test_static_complete_site_is_not_rendered(engine, tmp_path):
    """A site that verifies statically must never trigger a JS render."""
    _mosque(engine, "stat", "https://stat.example/")
    rendered_called = []

    def renderer(url):
        rendered_called.append(url)
        return RENDERED_TABLE

    fetcher = _js_fetcher(
        static_pages={
            "https://stat.example/": HOME_WITH_LINKS,
            "https://stat.example/prayer-times": RENDERED_TABLE,
        },
        rendered_pages={"https://stat.example/prayer-times": RENDERED_TABLE},
    )
    out = discover_mosque(engine, "stat", fetcher=fetcher, client=_live_client(),
                          candidate_root=tmp_path, today=date(2026, 6, 1), horizon_days=20,
                          renderer=renderer)
    assert out.outcome == "authored"
    assert rendered_called == []  # static success → no render


# ---------------------------------------------------------------------------
# Unit coverage for the escalation predicate.
# ---------------------------------------------------------------------------
def test_page_needs_render_flags_shell_table():
    assert _page_needs_render("https://x/prayer-times", SHELL_TABLE) is True


def test_page_needs_render_skips_complete_table():
    assert _page_needs_render("https://x/prayer-times", RENDERED_TABLE) is False


def test_page_needs_render_skips_irrelevant_page():
    plain = "<html><body><h1>Welcome</h1><p>About our community</p></body></html>"
    assert _page_needs_render("https://x/about", plain) is False
