import httpx

from directory import repository as repo
from directory.db import session_scope
from directory.ingest.discover import discover_mosque, run_discovery
from directory.models import Mosque

WP_HTML = """
<html><head><meta name="generator" content="WordPress"></head><body>
<table class="dpt_table">
  <tr><th>Date</th><th>Fajr</th><th>Dhuhr</th><th>Asr</th><th>Maghrib</th><th>Isha</th></tr>
  <tr><td>1</td><td>05:00</td><td>13:15</td><td>18:30</td><td>21:10</td><td>22:30</td></tr>
  <tr><td>2</td><td>05:02</td><td>13:16</td><td>18:31</td><td>21:11</td><td>22:31</td></tr>
</table></body></html>
"""

PLAIN_HTML = '<html><body><a href="/prayer-times">Prayer Times</a></body></html>'
TIMES_HTML = (
    "<html><body><table><tr><th>Fajr</th></tr>"
    "<tr><td>05:00</td></tr></table></body></html>"
)


def _fetcher_for(pages):
    from directory.ingest.fetch import FetchResult

    def _f(url, *, requires_js=False, etag=None, last_modified=None, client=None,
           renderer=None, timeout=20.0):
        html = pages.get(url)
        if html is None:
            return FetchResult(url, 404, None, None, error="404")
        return FetchResult(url, 200, html, "hash")

    return _f


def test_platform_match_authors_and_verifies(engine, tmp_path):
    with session_scope(engine) as s:
        s.add(Mosque(id="wp", name="wp", lat=51.0, lng=-1.0,
                     website_url="https://wp.example/"))
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, text="ok")),
                          follow_redirects=True)
    fetcher = _fetcher_for({"https://wp.example/": WP_HTML})

    from datetime import date

    out = discover_mosque(engine, "wp", fetcher=fetcher, client=client,
                          candidate_root=tmp_path, today=date(2026, 6, 1), horizon_days=20)
    assert out.outcome in {"authored", "review"}
    assert out.platform == "wp_prayer"
    with session_scope(engine) as s:
        src = repo.get_source(s, "wp")
        assert src.shape == "html_table"


def test_no_platform_gathers_candidate(engine, tmp_path):
    with session_scope(engine) as s:
        s.add(Mosque(id="plain", name="plain", lat=51.0, lng=-1.0,
                     website_url="https://plain.example/"))
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, text="ok")),
                          follow_redirects=True)
    fetcher = _fetcher_for({
        "https://plain.example/": PLAIN_HTML,
        "https://plain.example/prayer-times": TIMES_HTML,
    })

    out = discover_mosque(engine, "plain", fetcher=fetcher, client=client,
                          candidate_root=tmp_path)
    assert out.outcome == "candidate"
    assert (tmp_path / "plain.json").exists()
    with session_scope(engine) as s:
        assert repo.get_source(s, "plain").triage_status == "candidate"


def test_dead_site_nulls_website(engine, tmp_path):
    with session_scope(engine) as s:
        s.add(Mosque(id="dead", name="dead", lat=51.0, lng=-1.0,
                     website_url="https://dead.example/"))
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404)),
                          follow_redirects=True)
    out = discover_mosque(engine, "dead", client=client, candidate_root=tmp_path)
    assert out.outcome == "dead"
    with session_scope(engine) as s:
        assert repo.get_mosque(s, "dead").website_url is None


def test_blocklisted_after_redirect_short_circuits(engine, tmp_path):
    fetch_calls = []

    def _spy_fetcher(url, **kwargs):
        from directory.ingest.fetch import FetchResult
        fetch_calls.append(url)
        return FetchResult(url, 200, "<html></html>", "hash")

    with session_scope(engine) as s:
        s.add(Mosque(id="fb", name="fb", lat=51.0, lng=-1.0,
                     website_url="https://masjid.example/"))
    # liveness resolves the seed to a facebook page (redirect target is blocklisted)
    def _handler(r):
        if "masjid.example" in str(r.url):
            return httpx.Response(302, headers={"Location": "https://www.facebook.com/x"})
        return httpx.Response(200, text="ok")

    client = httpx.Client(
        transport=httpx.MockTransport(_handler), follow_redirects=True
    )
    out = discover_mosque(engine, "fb", fetcher=_spy_fetcher, client=client,
                          candidate_root=tmp_path)
    assert out.outcome == "blocklisted"
    assert fetch_calls == []  # no fetch, no AI
    with session_scope(engine) as s:
        assert repo.get_source(s, "fb").triage_status == "blocklisted"


def test_run_discovery_covers_all(engine, tmp_path):
    with session_scope(engine) as s:
        s.add_all([
            Mosque(id="wp", name="wp", lat=51.0, lng=-1.0, website_url="https://wp.example/"),
            Mosque(id="no", name="no", lat=51.0, lng=-1.0, website_url=None),
        ])
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, text="ok")),
                          follow_redirects=True)
    fetcher = _fetcher_for({"https://wp.example/": WP_HTML})
    from datetime import date

    outs = run_discovery(engine, fetcher=fetcher, client=client, candidate_root=tmp_path,
                         today=date(2026, 6, 1), horizon_days=20)
    assert {o.mosque_id for o in outs} == {"wp"}  # the null-website mosque is skipped
