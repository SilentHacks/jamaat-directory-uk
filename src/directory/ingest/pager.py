"""Multi-month crawling for sources whose timetable spans monthly pages.

A `paging` config turns one source into several documents — one per month in
the horizon — fetched either from a templated URL (`url_template`) or by driving
a headless browser through a JS calendar (`render_nav`). Sources without a
`paging` config collapse to the single-document legacy path, so the rest of the
pipeline (extract → materialize → gates → persist) is unchanged.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta

from directory.ingest.extractors.config_schema import NavSpec, SourceConfig
from directory.ingest.extractors.engine import ExtractionResult, extract

# (base_url, nav, count) -> one HTML document per month, current month first.
NavRenderer = Callable[[str, NavSpec, int], list[str]]


@dataclass
class MonthDoc:
    year: int
    month: int
    html: str


def months_in_horizon(today: date, horizon_days: int) -> list[tuple[int, int]]:
    """(year, month) pairs covering ``today`` through ``today + horizon_days``,
    inclusive of the end month. The current month is always first."""
    end = today + timedelta(days=horizon_days)
    out: list[tuple[int, int]] = []
    y, m = today.year, today.month
    while (y, m) <= (end.year, end.month):
        out.append((y, m))
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return out


def collect_documents(
    config: SourceConfig,
    url: str | None,
    *,
    today: date,
    horizon_days: int,
    requires_js: bool,
    fetcher,
    renderer=None,
    nav_renderer: NavRenderer | None = None,
) -> tuple[list[MonthDoc], str | None]:
    """Fetch the document(s) a source needs for the horizon.

    Returns ``(docs, error)``. ``error`` is set (and ``docs`` empty) only when the
    *current* month cannot be obtained — a missing future month is tolerated and
    simply shortens the horizon, since it fills in on a later day's run.
    """
    months = months_in_horizon(today, horizon_days)
    paging = config.paging

    if paging is None:
        res = fetcher(url, requires_js=requires_js, renderer=renderer)
        if res.error or not res.html:
            return [], res.error or "empty body"
        return [MonthDoc(today.year, today.month, res.html)], None

    if paging.mode == "url_template":
        docs: list[MonthDoc] = []
        for idx, (y, m) in enumerate(months):
            target = paging.url_template.format(year=y, month=m)
            res = fetcher(target, requires_js=requires_js, renderer=renderer)
            if res.error or not res.html:
                if idx == 0:  # current month is required
                    return [], res.error or "empty body"
                continue  # future month not yet published → tolerate
            docs.append(MonthDoc(y, m, res.html))
        return docs, None

    # render_nav: one browser session walks forward through the months.
    if nav_renderer is None:
        return [], "render_nav config requires a navigation renderer"
    try:
        htmls = nav_renderer(url, paging.nav, len(months))
    except Exception as exc:
        return [], f"nav render failed: {type(exc).__name__}: {exc}"
    docs = [MonthDoc(y, m, h) for (y, m), h in zip(months, htmls, strict=False) if h]
    if not docs:
        return [], "navigation produced no documents"
    return docs, None


def extract_documents(
    docs: list[MonthDoc], config: SourceConfig, *, today: date
) -> ExtractionResult:
    """Extract each document with its own (year, month) and merge the cells.

    Overlapping months are harmless: materialize keys by (date, prayer), so a day
    appearing on two pages resolves last-wins rather than duplicating."""
    merged = ExtractionResult()
    for doc in docs:
        result = extract(doc.html, config, year=doc.year, month=doc.month, today=today)
        merged.cells.extend(result.cells)
        merged.texts.extend(result.texts)
        merged.warnings.extend(result.warnings)
    return merged
