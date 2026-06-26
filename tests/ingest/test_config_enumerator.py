from datetime import date

from directory.ingest.config_enumerator import (
    best_verified_candidate,
    detect_candidates,
    enumerate_candidates,
)
from directory.ingest.evidence import NavHint, build_page_evidence
from directory.ingest.fetch import FetchResult

TODAY = date(2026, 6, 1)
URL = "https://m.example/prayer-times"

MONTHLY_TABLE = (
    "<table class='pt'><tr><th>Date</th><th>Fajr</th><th>Dhuhr</th><th>Asr</th>"
    "<th>Maghrib</th><th>Isha</th></tr>"
    "<tr><td>1 June</td><td>05:00</td><td>13:30</td><td>18:30</td><td>21:30</td><td>23:00</td></tr>"
    "<tr><td>2 June</td><td>05:02</td><td>13:31</td><td>18:31</td><td>21:31</td><td>23:01</td></tr>"
    "</table>"
)

TRANSPOSED_TABLE = (
    "<table class='tr'><tr><th>Prayer</th><th>1 June</th><th>2 June</th></tr>"
    "<tr><td>Fajr</td><td>05:00</td><td>05:01</td></tr>"
    "<tr><td>Dhuhr</td><td>13:30</td><td>13:31</td></tr>"
    "<tr><td>Asr</td><td>18:30</td><td>18:31</td></tr>"
    "<tr><td>Maghrib</td><td>21:30</td><td>21:31</td></tr>"
    "<tr><td>Isha</td><td>23:00</td><td>23:01</td></tr></table>"
)

SINGLE_DAY_TABLE = (
    "<table class='d'><tr><th>Fajr</th><th>Dhuhr</th><th>Asr</th><th>Maghrib</th>"
    "<th>Isha</th></tr>"
    "<tr><td>05:00</td><td>13:30</td><td>18:30</td><td>21:30</td><td>23:00</td></tr></table>"
)

VERTICAL_WIDGET = (
    "<table class='v'><tr><th>Prayer</th><th>Begins</th><th>Jamaah</th></tr>"
    "<tr><td>Fajr</td><td>04:45</td><td>05:00</td></tr>"
    "<tr><td>Dhuhr</td><td>13:00</td><td>13:30</td></tr>"
    "<tr><td>Asr</td><td>18:00</td><td>18:30</td></tr>"
    "<tr><td>Maghrib</td><td>21:00</td><td>21:30</td></tr>"
    "<tr><td>Isha</td><td>22:30</td><td>23:00</td></tr></table>"
)

PDF_PAGE = (
    '<html><body><a href="/timetable-june-2026.pdf">June 2026 Prayer Timetable</a>'
    "</body></html>"
)
IMAGE_PAGE = (
    '<html><body><img src="/prayer-timetable-2026.png" alt="Prayer timetable 2026">'
    "</body></html>"
)
UNKNOWN_WIDGET_PAGE = (
    '<html><body><iframe src="https://unknown-widget.example/embed"></iframe></body></html>'
)


def _fetcher_returning(html):
    def _f(url, **kwargs):
        return FetchResult(url, 200, html, "h", error=None)

    return _f


def _evidence(html, url=URL):
    return build_page_evidence(html, url, today=TODAY)


def _sources(candidates):
    return [c.source for c in candidates]


# ── enumeration shape ─────────────────────────────────────────────────────────


def test_simple_monthly_table_enumerates_horizontal_multiday():
    cands = enumerate_candidates([_evidence(MONTHLY_TABLE)])
    assert "enumerator:table_horizontal_multiday" in _sources(cands)


def test_transposed_monthly_table_enumerates_transpose():
    cands = enumerate_candidates([_evidence(TRANSPOSED_TABLE)])
    assert "enumerator:table_transpose_multiday" in _sources(cands)


def test_single_day_horizontal_table_enumerates_single_day():
    cands = enumerate_candidates([_evidence(SINGLE_DAY_TABLE)])
    assert "enumerator:table_horizontal_single_day" in _sources(cands)


def test_vertical_widget_enumerates_vertical_single_day():
    cands = enumerate_candidates([_evidence(VERTICAL_WIDGET)])
    assert "enumerator:table_vertical_single_day" in _sources(cands)


def test_media_only_pdf_is_enumerated():
    cands = enumerate_candidates([_evidence(PDF_PAGE)])
    media = [c for c in cands if c.source == "enumerator:media_pdf"]
    assert media and media[0].config.shape == "pdf"
    assert media[0].config.media.url.endswith("/timetable-june-2026.pdf")


def test_media_only_image_is_enumerated():
    cands = enumerate_candidates([_evidence(IMAGE_PAGE)])
    assert "enumerator:media_image" in _sources(cands)


def test_unknown_widget_is_not_emitted():
    cands = enumerate_candidates([_evidence(UNKNOWN_WIDGET_PAGE)])
    assert not any(c.source.startswith("enumerator:widget") for c in cands)


# ── paging only attaches to multi-day layouts ─────────────────────────────────


def test_paging_emitted_for_multiday_table_with_month_nav():
    ev = _evidence(MONTHLY_TABLE)
    ev.nav_hints = [NavHint(kind="next", next_selector="text=›")]
    assert any(c.source.endswith("_paged") for c in enumerate_candidates([ev]))


def test_paging_not_emitted_for_single_day_with_month_nav():
    ev = _evidence(SINGLE_DAY_TABLE)
    ev.nav_hints = [NavHint(kind="next", next_selector="text=›")]
    assert not any(c.source.endswith("_paged") for c in enumerate_candidates([ev]))


def test_paging_not_emitted_for_prayer_rows_with_month_nav():
    ev = _evidence(VERTICAL_WIDGET)
    ev.nav_hints = [NavHint(kind="next", next_selector="text=›")]
    assert not any(c.source.endswith("_paged") for c in enumerate_candidates([ev]))


# ── verification + ranking ────────────────────────────────────────────────────


def test_best_verified_authors_a_monthly_table():
    cands = enumerate_candidates([_evidence(MONTHLY_TABLE)])
    best = best_verified_candidate(
        cands, today=TODAY, horizon_days=5, fetcher=_fetcher_returning(MONTHLY_TABLE)
    )
    assert best is not None
    assert best.triage_status == "authored"
    assert best.candidate.source == "enumerator:table_horizontal_multiday"


def test_best_verified_defers_a_media_candidate():
    cands = enumerate_candidates([_evidence(PDF_PAGE)])
    # media never fetches; any fetcher is fine
    best = best_verified_candidate(cands, today=TODAY, horizon_days=14,
                                   fetcher=_fetcher_returning(""))
    assert best is not None
    assert best.triage_status == "deferred_media"


def test_best_verified_returns_none_when_nothing_verifies():
    cands = enumerate_candidates([_evidence(MONTHLY_TABLE)])
    # the live page no longer contains the table → zero rows → nothing verifies
    best = best_verified_candidate(
        cands, today=TODAY, horizon_days=5,
        fetcher=_fetcher_returning("<html><body>gone</body></html>"),
    )
    assert best is None


def test_structured_table_outranks_media_when_both_verify():
    page = MONTHLY_TABLE + PDF_PAGE
    cands = enumerate_candidates([_evidence(page)])
    best = best_verified_candidate(
        cands, today=TODAY, horizon_days=5, fetcher=_fetcher_returning(page)
    )
    assert best is not None
    assert not best.evaluation.media  # the HTML table wins over the PDF link


def test_detect_candidates_wraps_platform_match():
    cands = detect_candidates({URL: MONTHLY_TABLE})
    assert cands and cands[0].platform == "generic_table"
