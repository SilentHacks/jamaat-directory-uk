"""Detector for per-day record-stream timetables (see ``dom_records`` engine).

These layouts only exist after JavaScript renders them, so a match always sets
``requires_js``. When the page is a single-month calendar with a forward control,
the detector also emits a ``render_nav`` paging spec so the daily extract walks
the whole horizon — reusing the existing pager + headless navigation renderer.
"""

from datetime import date

from bs4 import BeautifulSoup, Tag

from directory.ingest.extractors.config_schema import (
    DateSpec,
    GridSpec,
    NavSpec,
    PagingSpec,
    SourceConfig,
)
from directory.ingest.extractors.dom_records import extract_dom_records
from directory.ingest.extractors.platforms.base import PlatformMatch
from directory.ingest.normalize import month_from_text, normalize_token

# A record stream must name at least this many distinct daily prayers to be a
# timetable rather than incidental prayer text; the gates then enforce
# plausibility, ordering and completeness.
_MIN_PRAYERS = 3

# Single-glyph / word controls that step a JS calendar forward one month.
_NEXT_TOKENS = ("›", "»", "→", "❯", "＞", ">", "next", "next month")
_MONTH_OPTIONS_MIN = 12


def _month_caption(leaves: list[Tag]) -> bool:
    """A bare month label (optionally with a year), e.g. "June 2026" — the caption
    of a single-month calendar view."""
    return any(month_from_text(el.get_text(" ", strip=True)) is not None for el in leaves)


def _month_select(soup: BeautifulSoup) -> str | None:
    """CSS selector for a <select> whose options are month names, if any."""
    for sel in soup.find_all("select"):
        months = sum(
            1
            for opt in sel.find_all("option")
            if month_from_text(opt.get_text(" ", strip=True)) is not None
        )
        if months >= _MONTH_OPTIONS_MIN:
            if sel.get("id"):
                return f"select#{sel.get('id')}"
            if sel.get("name"):
                return f"select[name='{sel.get('name')}']"
            classes = sel.get("class") or []
            if classes:
                return "select." + classes[0]
            return "select"
    return None


def _next_control(leaves: list[Tag]) -> str | None:
    """A Playwright ``text=`` selector for a forward (next-month) control."""
    for el in leaves:
        token = normalize_token(el.get_text(" ", strip=True))
        raw = el.get_text(" ", strip=True)
        if raw in _NEXT_TOKENS or token in _NEXT_TOKENS:
            return f"text={raw}"
    return None


def _detect_month_nav(soup: BeautifulSoup, leaves: list[Tag]) -> PagingSpec | None:
    """A ``render_nav`` paging spec when the page is a single-month view with a
    month control. Prefers a month <select> (precise) over a forward glyph."""
    if not _month_caption(leaves):
        return None
    month_select = _month_select(soup)
    if month_select is not None:
        return PagingSpec(mode="render_nav", nav=NavSpec(kind="select", month_select=month_select))
    nxt = _next_control(leaves)
    if nxt is not None:
        return PagingSpec(mode="render_nav", nav=NavSpec(kind="next", next_selector=nxt))
    return None


class DomRecordsDetector:
    name = "dom_records"

    def detect(self, html: str, url: str, *, today: date | None = None) -> PlatformMatch | None:
        run_day = today or date.today()
        grid = GridSpec(date=DateSpec(format="d_month"))
        config = SourceConfig(shape="dom_records", grid=grid)
        result = extract_dom_records(
            html, config, year=run_day.year, month=run_day.month, today=run_day
        )
        prayers = {c.prayer for c in result.cells if c.kind == "jamaah"}
        days = {c.date for c in result.cells}
        if len(prayers) < _MIN_PRAYERS or not days:
            return None

        # Paging only makes sense for a multi-day, date-bearing view.
        if len(days) >= 2:
            soup = BeautifulSoup(html, "lxml")
            for tag in soup(["script", "style", "noscript", "template"]):
                tag.decompose()
            paging = _detect_month_nav(soup, [el for el in soup.find_all(True)
                                              if el.get_text(strip=True) and not el.find(True)])
            config = SourceConfig(shape="dom_records", grid=grid, paging=paging)

        return PlatformMatch(platform=self.name, url=url, requires_js=True, config=config)
