import httpx

from directory import repository as repo
from directory.db import session_scope
from directory.ingest.discover import discover_mosque, run_discovery
from directory.models import Mosque, Source

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
    "<html><body><table><tr><th>Fajr</th></tr><tr><td>05:00</td></tr></table></body></html>"
)


def _fetcher_for(pages):
    from directory.ingest.fetch import FetchResult

    def _f(
        url,
        *,
        requires_js=False,
        etag=None,
        last_modified=None,
        client=None,
        renderer=None,
        timeout=20.0,
    ):
        html = pages.get(url)
        if html is None:
            return FetchResult(url, 404, None, None, error="404")
        return FetchResult(url, 200, html, "hash")

    return _f


def test_platform_match_authors_and_verifies(engine, tmp_path):
    with session_scope(engine) as s:
        s.add(Mosque(id="wp", name="wp", lat=51.0, lng=-1.0, website_url="https://wp.example/"))
    client = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text="ok")),
        follow_redirects=True,
    )
    fetcher = _fetcher_for({"https://wp.example/": WP_HTML})

    from datetime import date

    out = discover_mosque(
        engine,
        "wp",
        fetcher=fetcher,
        client=client,
        candidate_root=tmp_path,
        today=date(2026, 6, 1),
        horizon_days=20,
    )
    assert out.outcome in {"authored", "review"}
    assert out.platform == "wp_prayer"
    with session_scope(engine) as s:
        src = repo.get_source(s, "wp")
        assert src.shape == "html_table"


def test_no_platform_gathers_candidate(engine, tmp_path):
    with session_scope(engine) as s:
        s.add(
            Mosque(
                id="plain", name="plain", lat=51.0, lng=-1.0, website_url="https://plain.example/"
            )
        )
    client = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text="ok")),
        follow_redirects=True,
    )
    fetcher = _fetcher_for(
        {
            "https://plain.example/": PLAIN_HTML,
            "https://plain.example/prayer-times": TIMES_HTML,
        }
    )

    out = discover_mosque(engine, "plain", fetcher=fetcher, client=client, candidate_root=tmp_path)
    assert out.outcome == "candidate"
    assert (tmp_path / "plain.json").exists()
    with session_scope(engine) as s:
        assert repo.get_source(s, "plain").triage_status == "candidate"


def test_dead_site_nulls_website(engine, tmp_path):
    with session_scope(engine) as s:
        s.add(
            Mosque(id="dead", name="dead", lat=51.0, lng=-1.0, website_url="https://dead.example/")
        )
    client = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(404)), follow_redirects=True
    )
    out = discover_mosque(engine, "dead", client=client, candidate_root=tmp_path)
    assert out.outcome == "dead"
    with session_scope(engine) as s:
        assert repo.get_mosque(s, "dead").website_url is None


HOME_WITH_LINKS = (
    '<html><body><a href="/prayer-times">Prayer Times</a>'
    '<a href="/timetable">Timetable</a></body></html>'
)

GENERIC_TABLE = """
<html><body><table>
  <tr><th>Date</th><th>Fajr</th><th>Dhuhr</th><th>Asr</th><th>Maghrib</th><th>Isha</th></tr>
  <tr><td>1 June</td><td>05:00</td><td>13:15</td><td>18:30</td><td>21:10</td><td>22:30</td></tr>
  <tr><td>2 June</td><td>05:02</td><td>13:16</td><td>18:31</td><td>21:11</td><td>22:31</td></tr>
  <tr><td>3 June</td><td>05:03</td><td>13:17</td><td>18:32</td><td>21:12</td><td>22:32</td></tr>
</table></body></html>
"""

PARTIAL_TABLE = """
<html><body><table>
  <tr><th>Date</th><th>Fajr</th><th>Dhuhr</th><th>Asr</th></tr>
  <tr><td>1 June</td><td>05:00</td><td>13:15</td><td>18:30</td></tr>
  <tr><td>2 June</td><td>05:02</td><td>13:16</td><td>18:31</td></tr>
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


def test_platform_on_subpage_authors(engine, tmp_path):
    from datetime import date

    _mosque(engine, "wpsub", "https://wpsub.example/")
    fetcher = _fetcher_for(
        {
            "https://wpsub.example/": HOME_WITH_LINKS,
            "https://wpsub.example/prayer-times": WP_HTML,
        }
    )
    out = discover_mosque(
        engine,
        "wpsub",
        fetcher=fetcher,
        client=_live_client(),
        candidate_root=tmp_path,
        today=date(2026, 6, 1),
        horizon_days=20,
    )
    assert out.outcome in {"authored", "review"}
    assert out.platform == "wp_prayer"


def test_generic_table_on_subpage_authors(engine, tmp_path):
    from datetime import date

    _mosque(engine, "gen", "https://gen.example/")
    fetcher = _fetcher_for(
        {
            "https://gen.example/": HOME_WITH_LINKS,
            "https://gen.example/timetable": GENERIC_TABLE,
        }
    )
    out = discover_mosque(
        engine,
        "gen",
        fetcher=fetcher,
        client=_live_client(),
        candidate_root=tmp_path,
        today=date(2026, 6, 1),
        horizon_days=20,
    )
    assert out.outcome == "authored"
    assert out.platform == "generic_table"


def test_partial_table_routes_to_review(engine, tmp_path):
    from datetime import date

    _mosque(engine, "part", "https://part.example/")
    fetcher = _fetcher_for(
        {
            "https://part.example/": HOME_WITH_LINKS,
            "https://part.example/timetable": PARTIAL_TABLE,
        }
    )
    out = discover_mosque(
        engine,
        "part",
        fetcher=fetcher,
        client=_live_client(),
        candidate_root=tmp_path,
        today=date(2026, 6, 1),
        horizon_days=20,
    )
    assert out.outcome == "review"
    assert out.platform == "generic_table"
    with session_scope(engine) as s:
        assert repo.get_source(s, "part").triage_status == "review"


def test_nothing_found_falls_through_to_candidate(engine, tmp_path):
    _mosque(engine, "none", "https://none.example/")
    fetcher = _fetcher_for({"https://none.example/": HOME_WITH_LINKS})
    out = discover_mosque(
        engine, "none", fetcher=fetcher, client=_live_client(), candidate_root=tmp_path
    )
    assert out.outcome == "candidate"
    assert (tmp_path / "none.json").exists()


def test_blocklisted_after_redirect_short_circuits(engine, tmp_path):
    fetch_calls = []

    def _spy_fetcher(url, **kwargs):
        from directory.ingest.fetch import FetchResult

        fetch_calls.append(url)
        return FetchResult(url, 200, "<html></html>", "hash")

    with session_scope(engine) as s:
        s.add(Mosque(id="fb", name="fb", lat=51.0, lng=-1.0, website_url="https://masjid.example/"))

    # liveness resolves the seed to a facebook page (redirect target is blocklisted)
    def _handler(r):
        if "masjid.example" in str(r.url):
            return httpx.Response(302, headers={"Location": "https://www.facebook.com/x"})
        return httpx.Response(200, text="ok")

    client = httpx.Client(transport=httpx.MockTransport(_handler), follow_redirects=True)
    out = discover_mosque(
        engine, "fb", fetcher=_spy_fetcher, client=client, candidate_root=tmp_path
    )
    assert out.outcome == "blocklisted"
    assert fetch_calls == []  # no fetch, no AI
    with session_scope(engine) as s:
        assert repo.get_source(s, "fb").triage_status == "blocklisted"


def test_discover_preserves_existing_config(engine, tmp_path):
    """Re-running discovery on a source that already holds a config must not fetch
    or wipe it — the config-clobber footgun. It short-circuits to 'skipped'."""
    _mosque(engine, "keep", "https://keep.example/")
    with session_scope(engine) as s:
        s.add(
            Source(
                id="keep",
                mosque_id="keep",
                url="https://keep.example/t",
                shape="html_table",
                platform="generic_table",
                config='{"shape":"html_table"}',
                triage_status="needs_reauthor",
            )
        )

    fetch_calls = []

    def _spy(url, **kwargs):
        from directory.ingest.fetch import FetchResult

        fetch_calls.append(url)
        return FetchResult(url, 200, WP_HTML, "h")

    out = discover_mosque(
        engine, "keep", fetcher=_spy, client=_live_client(), candidate_root=tmp_path
    )

    assert out.outcome == "skipped"
    assert out.platform == "generic_table"
    assert fetch_calls == []  # no fetch, no liveness probe, config untouched
    with session_scope(engine) as s:
        src = repo.get_source(s, "keep")
        assert src.config == '{"shape":"html_table"}'
        assert src.triage_status == "needs_reauthor"


def test_discover_force_overwrites_existing_config(engine, tmp_path):
    from datetime import date

    _mosque(engine, "force", "https://force.example/")
    with session_scope(engine) as s:
        s.add(
            Source(
                id="force",
                mosque_id="force",
                url="https://force.example/old",
                shape="html_table",
                config='{"old":true}',
                triage_status="needs_reauthor",
            )
        )
    fetcher = _fetcher_for({"https://force.example/": WP_HTML})

    out = discover_mosque(
        engine,
        "force",
        fetcher=fetcher,
        client=_live_client(),
        candidate_root=tmp_path,
        today=date(2026, 6, 1),
        horizon_days=20,
        force=True,
    )

    assert out.outcome in {"authored", "review"}  # re-discovered despite prior config
    with session_scope(engine) as s:
        assert repo.get_source(s, "force").config != '{"old":true}'


def test_run_discovery_covers_all(engine, tmp_path):
    with session_scope(engine) as s:
        s.add_all(
            [
                Mosque(id="wp", name="wp", lat=51.0, lng=-1.0, website_url="https://wp.example/"),
                Mosque(id="no", name="no", lat=51.0, lng=-1.0, website_url=None),
            ]
        )
    client = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text="ok")),
        follow_redirects=True,
    )
    fetcher = _fetcher_for({"https://wp.example/": WP_HTML})
    from datetime import date

    outs = run_discovery(
        engine,
        fetcher=fetcher,
        client=client,
        candidate_root=tmp_path,
        today=date(2026, 6, 1),
        horizon_days=20,
    )
    assert {o.mosque_id for o in outs} == {"wp"}  # the null-website mosque is skipped


PDF_ONLY_PAGE = (
    "<html><body><h1>Prayer Times</h1>"
    '<a href="/june-2026-prayer-timetable.pdf">June 2026 Prayer Timetable</a>'
    "</body></html>"
)


def test_media_only_page_defers_via_enumerator_without_ai(engine, tmp_path):
    """A page whose timetable is only a (clearly named) PDF is recovered by the
    deterministic enumerator as deferred_media. The candidate bundle is still
    written so that a later forced re-authoring sees the evidence that justified
    the deterministic outcome."""
    from datetime import date

    _mosque(engine, "pdf", "https://pdf.example/")
    fetcher = _fetcher_for({"https://pdf.example/": PDF_ONLY_PAGE})

    out = discover_mosque(
        engine,
        "pdf",
        fetcher=fetcher,
        client=_live_client(),
        candidate_root=tmp_path,
        today=date(2026, 6, 1),
        horizon_days=20,
    )

    assert out.outcome == "deferred_media"
    assert (tmp_path / "pdf.json").exists()  # bundle kept for forced re-authoring
    with session_scope(engine) as s:
        src = repo.get_source(s, "pdf")
        assert src.triage_status == "deferred_media"
        assert src.shape == "pdf"
        assert "june-2026-prayer-timetable.pdf" in src.config
