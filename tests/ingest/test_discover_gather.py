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


# A page that leads with a thin single-day widget but also carries a full
# multi-day timetable further down — discovery must hand the AI the richer one.
RICH_VS_POOR = """
<html><body>
<table id="widget">
  <tr><th>Fajr</th><th>Dhuhr</th><th>Asr</th><th>Maghrib</th><th>Isha</th></tr>
  <tr><td>05:00</td><td>13:00</td><td>18:00</td><td>21:00</td><td>22:30</td></tr>
</table>
<table id="month">
  <tr><th>Day</th><th>Fajr</th><th>Dhuhr</th><th>Asr</th><th>Maghrib</th><th>Isha</th></tr>
  <tr><td>1</td><td>05:01</td><td>13:01</td><td>18:01</td><td>21:01</td><td>22:31</td></tr>
  <tr><td>2</td><td>05:02</td><td>13:02</td><td>18:02</td><td>21:02</td><td>22:32</td></tr>
  <tr><td>3</td><td>05:03</td><td>13:03</td><td>18:03</td><td>21:03</td><td>22:33</td></tr>
</table>
</body></html>
"""


def test_strip_to_region_picks_richest_prayer_table():
    region, _ = strip_to_region(RICH_VS_POOR)
    # the multi-day table (more time rows) wins over the first single-day widget
    assert 'id="month"' in region
    assert 'id="widget"' not in region


def test_strip_to_region_falls_back_to_body_without_prayer_table():
    region, text = strip_to_region("<html><body><p>no prayer data here</p></body></html>")
    assert "no prayer data here" in text


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
