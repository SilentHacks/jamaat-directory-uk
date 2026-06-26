from directory.ingest.extractors.platforms.iframe_widgets import MASJIDBOX

# A homepage embedding a Masjidbox iframe.
EMBED_HTML = """
<html><body>
<iframe src="//masjidbox.com/widget/abc123"></iframe>
</body></html>
"""

# A my-masjid embed must NOT be claimed by the iframe detector — my-masjid is handled
# by the verified JSON-API extractor in my_masjid.py.
MYMASJID_HTML = """
<html><body>
<iframe src="//time.my-masjid.com/embed/abc123"></iframe>
</body></html>
"""


def test_masjidbox_detects_and_resolves_iframe_src():
    match = MASJIDBOX.detect(EMBED_HTML, "https://m.example/")
    assert match is not None
    assert match.platform == "masjidbox"
    assert match.url == "https://masjidbox.com/widget/abc123"
    assert match.requires_js is True
    assert match.config.shape == "html_repeated"


def test_masjidbox_requires_its_own_domain():
    assert MASJIDBOX.detect(MYMASJID_HTML, "https://m.example/") is None
