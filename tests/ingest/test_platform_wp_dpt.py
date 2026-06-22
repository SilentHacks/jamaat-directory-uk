import re
from pathlib import Path

from directory.ingest.extractors.platforms.wp_dpt import WpDptDetector
from directory.ingest.fetch import FetchResult

FIXTURES = Path(__file__).parent.parent / "fixtures"
WIDGET = (FIXTURES / "dpt_widget_page.html").read_text()
JUNE = (FIXTURES / "dpt_month_jun.html").read_text()
JULY = (FIXTURES / "dpt_month_jul.html").read_text()


def _fetcher(url, *, requires_js=False, etag=None, last_modified=None, client=None,
             renderer=None, timeout=20.0):
    if "get_monthly_timetable" in url:
        m = re.search(r"month=(\d+)", url)
        month = int(m.group(1)) if m else 6
        return FetchResult(url, 200, JULY if month == 7 else JUNE, "h")
    return FetchResult(url, 404, "", None)


def test_detects_dpt_and_emits_url_template_html_table():
    match = WpDptDetector().detect(WIDGET, "https://m.example/timetable/", fetcher=_fetcher)
    assert match is not None
    assert match.platform == "wp_dpt"
    assert match.requires_js is False

    config = match.config
    assert config.shape == "html_table"
    assert config.paging is not None
    assert config.paging.mode == "url_template"
    template = config.paging.url_template
    assert "action=get_monthly_timetable" in template
    assert "month={month}" in template
    # The endpoint is taken from timetable_params.ajaxurl on the page.
    assert template.startswith("https://m.example/wp-admin/admin-ajax.php?")

    begins = {c.prayer for c in config.grid.columns if c.kind == "begin"}
    jamaahs = {c.prayer for c in config.grid.columns if c.kind == "jamaah"}
    assert {p.value for p in begins} == {"fajr", "dhuhr", "asr", "maghrib", "isha"}
    assert {p.value for p in jamaahs} == {"fajr", "dhuhr", "asr", "maghrib", "isha"}


def test_parses_jumuah_sessions_from_widget():
    match = WpDptDetector().detect(WIDGET, "https://m.example/", fetcher=_fetcher)
    jumuah = match.config.jumuah
    assert jumuah is not None
    assert jumuah.source == "fixed"
    assert [s.time for s in jumuah.sessions] == ["13:30", "14:30"]
    assert jumuah.sessions[0].label.startswith("1st")
    assert jumuah.sessions[1].label.startswith("2nd")


def test_no_match_without_fetcher():
    # The month grid is only reachable via a fetch; without one, defer.
    assert WpDptDetector().detect(WIDGET, "https://m.example/", fetcher=None) is None


def test_no_match_on_unrelated_page():
    html = "<html><body><p>Welcome to our mosque</p></body></html>"
    assert WpDptDetector().detect(html, "https://m.example/", fetcher=_fetcher) is None
