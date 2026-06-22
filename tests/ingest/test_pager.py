from datetime import date

from directory.ingest.extractors.config_schema import SourceConfig
from directory.ingest.fetch import FetchResult
from directory.ingest.pager import (
    MonthDoc,
    collect_documents,
    extract_documents,
    months_in_horizon,
)

# A day-only month table: the date column carries the day number, so the
# resolved date depends on the (year, month) the page is extracted under.
MONTH_TABLE = """
<html><body><table>
<tr><th>Date</th><th>Fajr</th></tr>
<tr><td>1</td><td>05:00</td></tr>
<tr><td>2</td><td>05:02</td></tr>
</table></body></html>
"""

PAGED_CONFIG = SourceConfig.from_json(
    '{"shape":"html_table","grid":{"date":{"index":0,"format":"day_only"},'
    '"columns":[{"kind":"jamaah","prayer":"fajr","index":1}]},'
    '"paging":{"mode":"url_template","url_template":"https://x.org/{year}/{month:02d}"}}'
)

LEGACY_CONFIG = SourceConfig.from_json(
    '{"shape":"html_table","grid":{"date":{"index":0,"format":"day_only"},'
    '"columns":[{"kind":"jamaah","prayer":"fajr","index":1}]}}'
)

NAV_CONFIG = SourceConfig.from_json(
    '{"shape":"html_table","grid":{"date":{"index":0,"format":"day_only"},'
    '"columns":[{"kind":"jamaah","prayer":"fajr","index":1}]},'
    '"paging":{"mode":"render_nav","nav":{"kind":"next","next_selector":".n"}}}'
)


def _fetcher(pages):
    def fetcher(url, *, requires_js=False, etag=None, last_modified=None,
                client=None, renderer=None, timeout=20.0):
        if url in pages:
            return FetchResult(url, 200, pages[url], "h")
        return FetchResult(url, 404, None, None, error="not found")

    return fetcher


# ---------------------------------------------------------------------------
# months_in_horizon
# ---------------------------------------------------------------------------
def test_months_single_month_within_horizon():
    assert months_in_horizon(date(2026, 6, 5), 10) == [(2026, 6)]


def test_months_spans_month_boundary():
    assert months_in_horizon(date(2026, 6, 20), 60) == [(2026, 6), (2026, 7), (2026, 8)]


def test_months_crosses_year_boundary():
    assert months_in_horizon(date(2026, 12, 20), 20) == [(2026, 12), (2027, 1)]


def test_months_current_month_always_first():
    assert months_in_horizon(date(2026, 6, 1), 60)[0] == (2026, 6)


# ---------------------------------------------------------------------------
# collect_documents
# ---------------------------------------------------------------------------
def test_no_paging_returns_single_current_month_doc():
    fetcher = _fetcher({"https://m.org/times": MONTH_TABLE})
    docs, err = collect_documents(
        LEGACY_CONFIG, "https://m.org/times", today=date(2026, 6, 20),
        horizon_days=60, requires_js=False, fetcher=fetcher,
    )
    assert err is None
    assert [(d.year, d.month) for d in docs] == [(2026, 6)]


def test_no_paging_fetch_failure_is_an_error():
    fetcher = _fetcher({})  # nothing fetches
    docs, err = collect_documents(
        LEGACY_CONFIG, "https://m.org/times", today=date(2026, 6, 20),
        horizon_days=10, requires_js=False, fetcher=fetcher,
    )
    assert docs == [] and err == "not found"


def test_url_template_fetches_one_page_per_month():
    fetcher = _fetcher({
        "https://x.org/2026/06": MONTH_TABLE,
        "https://x.org/2026/07": MONTH_TABLE,
        "https://x.org/2026/08": MONTH_TABLE,
    })
    docs, err = collect_documents(
        PAGED_CONFIG, None, today=date(2026, 6, 20),
        horizon_days=60, requires_js=False, fetcher=fetcher,
    )
    assert err is None
    assert [(d.year, d.month) for d in docs] == [(2026, 6), (2026, 7), (2026, 8)]


def test_url_template_tolerates_missing_future_month():
    # August not yet published → horizon ends in July, no error.
    fetcher = _fetcher({
        "https://x.org/2026/06": MONTH_TABLE,
        "https://x.org/2026/07": MONTH_TABLE,
    })
    docs, err = collect_documents(
        PAGED_CONFIG, None, today=date(2026, 6, 20),
        horizon_days=60, requires_js=False, fetcher=fetcher,
    )
    assert err is None
    assert [(d.year, d.month) for d in docs] == [(2026, 6), (2026, 7)]


def test_url_template_current_month_failure_is_an_error():
    fetcher = _fetcher({"https://x.org/2026/07": MONTH_TABLE})  # only future month
    docs, err = collect_documents(
        PAGED_CONFIG, None, today=date(2026, 6, 20),
        horizon_days=60, requires_js=False, fetcher=fetcher,
    )
    assert docs == [] and err == "not found"


def test_render_nav_uses_nav_renderer():
    calls = {}

    def nav_renderer(url, nav, count):
        calls["url"], calls["count"], calls["kind"] = url, count, nav.kind
        return [MONTH_TABLE] * count

    docs, err = collect_documents(
        NAV_CONFIG, "https://js.org/cal", today=date(2026, 6, 20),
        horizon_days=60, requires_js=True, fetcher=_fetcher({}),
        nav_renderer=nav_renderer,
    )
    assert err is None
    assert [(d.year, d.month) for d in docs] == [(2026, 6), (2026, 7), (2026, 8)]
    assert calls == {"url": "https://js.org/cal", "count": 3, "kind": "next"}


def test_render_nav_tolerates_short_return():
    def nav_renderer(url, nav, count):
        return [MONTH_TABLE]  # only the current month came back

    docs, err = collect_documents(
        NAV_CONFIG, "https://js.org/cal", today=date(2026, 6, 20),
        horizon_days=60, requires_js=True, fetcher=_fetcher({}),
        nav_renderer=nav_renderer,
    )
    assert err is None
    assert [(d.year, d.month) for d in docs] == [(2026, 6)]


def test_render_nav_without_renderer_is_an_error():
    docs, err = collect_documents(
        NAV_CONFIG, "https://js.org/cal", today=date(2026, 6, 20),
        horizon_days=60, requires_js=True, fetcher=_fetcher({}),
        nav_renderer=None,
    )
    assert docs == [] and "navigation renderer" in err


def test_render_nav_renderer_crash_is_an_error():
    def nav_renderer(url, nav, count):
        raise RuntimeError("browser died")

    docs, err = collect_documents(
        NAV_CONFIG, "https://js.org/cal", today=date(2026, 6, 20),
        horizon_days=60, requires_js=True, fetcher=_fetcher({}),
        nav_renderer=nav_renderer,
    )
    assert docs == [] and "nav render failed" in err and "browser died" in err


# ---------------------------------------------------------------------------
# extract_documents
# ---------------------------------------------------------------------------
def test_extract_documents_resolves_each_doc_in_its_own_month():
    docs = [MonthDoc(2026, 6, MONTH_TABLE), MonthDoc(2026, 7, MONTH_TABLE)]
    result = extract_documents(docs, PAGED_CONFIG, today=date(2026, 6, 20))
    dates = sorted({c.date.isoformat() for c in result.cells})
    assert dates == ["2026-06-01", "2026-06-02", "2026-07-01", "2026-07-02"]
