import httpx

from directory.ingest.discover import (
    RANKED_PATHS,
    gather_candidates,
    strip_to_region,
)

HOMEPAGE = """
<html><body>
<a href="/prayer-times">Prayer Times</a>
<a href="/about">About</a>
<a href="https://other.example/x">Off-site</a>
</body></html>
"""

TIMES_PAGE = """
<html><body><nav>menu</nav>
<table class="t"><tr><th>Fajr</th></tr><tr><td>05:00</td></tr></table>
<footer>foot</footer></body></html>
"""


def test_strip_to_region_prefers_table():
    region, text = strip_to_region(TIMES_PAGE)
    assert "<table" in region
    assert "05:00" in text
    assert "menu" not in text


def test_gather_collects_and_scores_candidates():
    def handler(request):
        if request.url.path == "/prayer-times":
            return httpx.Response(200, text=TIMES_PAGE)
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    bundle = gather_candidates(
        "m1", "https://m.example/", homepage_html=HOMEPAGE, client=client
    )
    assert bundle.mosque_id == "m1"
    urls = [c.url for c in bundle.candidates]
    assert "https://m.example/prayer-times" in urls
    # candidate carrying a real table outranks empties
    top = bundle.candidates[0]
    assert "05:00" in top.text
    assert top.score > 0


def test_ranked_paths_include_common_slugs():
    assert "/prayer-times" in RANKED_PATHS
    assert "/timetable" in RANKED_PATHS
