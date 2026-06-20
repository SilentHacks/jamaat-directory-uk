from directory.domain import Prayer
from directory.ingest.extractors.engine import extract
from directory.ingest.extractors.platforms.iframe_widgets import MASJIDBOX, MYLOCALMASJID

# A homepage embedding a MyLocalMasjid iframe.
EMBED_HTML = """
<html><body>
<iframe src="//time.my-masjid.com/embed/abc123"></iframe>
</body></html>
"""

# The rendered iframe DOM the template targets (one item per day). The date is
# visible text (`span.d`) because the Phase-2 html_repeated engine reads
# el.get_text(), not attributes.
RENDERED = """
<html><body>
<div class="prayer-day">
  <span class="d">2026-06-01</span>
  <span class="p-fajr">05:00</span><span class="p-dhuhr">13:15</span>
  <span class="p-asr">18:30</span><span class="p-maghrib">21:10</span>
  <span class="p-isha">22:30</span>
</div>
<div class="prayer-day">
  <span class="d">2026-06-02</span>
  <span class="p-fajr">05:01</span><span class="p-dhuhr">13:15</span>
  <span class="p-asr">18:31</span><span class="p-maghrib">21:11</span>
  <span class="p-isha">22:31</span>
</div>
</body></html>
"""


def test_detects_and_resolves_iframe_src():
    match = MYLOCALMASJID.detect(EMBED_HTML, "https://m.example/")
    assert match is not None
    assert match.platform == "mylocalmasjid"
    assert match.url == "https://time.my-masjid.com/embed/abc123"
    assert match.requires_js is True
    assert match.config.shape == "html_repeated"


def test_emitted_config_extracts_rendered_dom():
    match = MYLOCALMASJID.detect(EMBED_HTML, "https://m.example/")
    result = extract(RENDERED, match.config, year=2026, month=6)
    fajr = [c for c in result.cells if c.prayer == Prayer.FAJR]
    assert {c.time for c in fajr} == {"05:00", "05:01"}


def test_masjidbox_requires_its_own_domain():
    assert MASJIDBOX.detect(EMBED_HTML, "https://m.example/") is None
