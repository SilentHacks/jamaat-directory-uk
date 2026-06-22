import re
from pathlib import Path

from directory.ingest.extractors.platforms.endpoint_month import EndpointMonthDetector
from directory.ingest.fetch import FetchResult

FIXTURES = Path(__file__).parent.parent / "fixtures"
JUNE = (FIXTURES / "dpt_month_jun.html").read_text()

# An unknown plugin that wires its own admin-ajax month call inline.
ENDPOINT_PAGE = """<html><body>
<h1>Prayer Timetable</h1>
<select id="m"></select>
<script>
  var ajax_obj = {"ajaxurl":"https://m.example/wp-admin/admin-ajax.php"};
  jQuery('#m').on('change', function () {
    jQuery.ajax({ url: ajax_obj.ajaxurl,
      data: { 'action': 'mptt_month_grid', 'month': this.value } });
  });
</script>
</body></html>"""

_MONTHS = ["January", "February", "March", "April", "May", "June",
           "July", "August", "September", "October", "November", "December"]
_OPTIONS = "".join(f"<option>{m}</option>" for m in _MONTHS)
_HEAD = "".join(f"<th>{h}</th>" for h in ("Date", "Fajr", "Dhuhr", "Asr", "Maghrib", "Isha"))
_ROWS = [
    ("1 June 2026", "03:45", "14:00", "19:30", "22:11", "23:15"),
    ("2 June 2026", "03:46", "14:00", "19:30", "22:12", "23:15"),
]
_BODY = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in _ROWS)
SELECT_PAGE = f"""<html><body>
<select id="month">{_OPTIONS}</select>
<table>
 <thead><tr>{_HEAD}</tr></thead>
 <tbody>{_BODY}</tbody>
</table>
</body></html>"""


def _endpoint_fetcher(url, *, requires_js=False, etag=None, last_modified=None,
                      client=None, renderer=None, timeout=20.0):
    if "action=mptt_month_grid" in url:
        return FetchResult(url, 200, JUNE, "h")
    return FetchResult(url, 404, "", None)


def test_3a_derives_admin_ajax_month_endpoint():
    match = EndpointMonthDetector().detect(
        ENDPOINT_PAGE, "https://m.example/prayers/", fetcher=_endpoint_fetcher
    )
    assert match is not None
    assert match.platform == "endpoint_month"
    assert match.requires_js is False
    paging = match.config.paging
    assert paging.mode == "url_template"
    assert "action=mptt_month_grid" in paging.url_template
    assert "month={month}" in paging.url_template
    assert match.config.shape == "html_table"
    assert len(match.config.grid.columns) >= 3


def test_3b_drives_month_select_via_render_nav():
    # No data endpoint on the page → fall back to driving the month <select>.
    match = EndpointMonthDetector().detect(SELECT_PAGE, "https://m.example/", fetcher=None)
    assert match is not None
    assert match.platform == "endpoint_month"
    assert match.requires_js is True
    paging = match.config.paging
    assert paging.mode == "render_nav"
    assert paging.nav.kind == "select"
    assert paging.nav.month_select == "select#month"
    assert match.config.shape == "html_table"


def test_no_match_on_plain_page():
    html = "<html><body><p>About our mosque</p></body></html>"
    match = EndpointMonthDetector().detect(html, "https://m.example/", fetcher=_endpoint_fetcher)
    assert match is None


def test_endpoint_url_carries_only_format_placeholders():
    match = EndpointMonthDetector().detect(
        ENDPOINT_PAGE, "https://m.example/prayers/", fetcher=_endpoint_fetcher
    )
    # The stored template must be safe for the pager's .format(year=, month=).
    template = match.config.paging.url_template
    assert set(re.findall(r"\{(\w+)", template)) <= {"month", "year"}
    template.format(year=2026, month=7)  # must not raise
