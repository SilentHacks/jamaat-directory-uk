"""Detector for per-day record-stream timetables (see ``dom_records`` engine).

These layouts only exist after JavaScript renders them, so a match always sets
``requires_js``. When the page is a single-month calendar with a forward control,
the detector also emits a ``render_nav`` paging spec so the daily extract walks
the whole horizon — reusing the existing pager + headless navigation renderer.
"""

from datetime import date

from bs4 import BeautifulSoup

from directory.ingest.extractors.config_schema import (
    DateSpec,
    GridSpec,
    SourceConfig,
)
from directory.ingest.extractors.dom_records import extract_dom_records
from directory.ingest.extractors.nav_detect import detect_month_nav, leaf_tags
from directory.ingest.extractors.platforms.base import PlatformMatch

# A record stream must name at least this many distinct daily prayers to be a
# timetable rather than incidental prayer text; the gates then enforce
# plausibility, ordering and completeness.
_MIN_PRAYERS = 3


class DomRecordsDetector:
    name = "dom_records"

    def detect(
        self, html: str, url: str, *, today: date | None = None, fetcher=None
    ) -> PlatformMatch | None:
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
            paging = detect_month_nav(soup, leaf_tags(soup))
            config = SourceConfig(shape="dom_records", grid=grid, paging=paging)

        return PlatformMatch(platform=self.name, url=url, requires_js=True, config=config)
