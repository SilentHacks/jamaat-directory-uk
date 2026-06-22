from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date

from bs4 import BeautifulSoup

from directory.domain import DAILY_PRAYERS, Prayer
from directory.ingest.extractors.config_schema import SourceConfig
from directory.ingest.extractors.tablegrid import caption_month, grid_matrix, row_month
from directory.ingest.normalize import parse_date, parse_offset, parse_time, resolve_prayer


@dataclass
class Cell:
    date: date
    prayer: Prayer
    kind: str  # "jamaah" | "begin"
    time: str | None  # "HH:MM"; None when the cell is a relative offset
    header_seen: str | None = None
    offset_min: int | None = None  # set when the cell is "+N" minutes
    base_prayer: Prayer | None = None  # begin time the offset resolves against


@dataclass
class ExtractionResult:
    cells: list[Cell] = field(default_factory=list)
    texts: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _prefer_pm(prayer: Prayer | None) -> bool | None:
    if prayer is None:
        return None
    return prayer != Prayer.FAJR


WidgetExtractor = Callable[..., ExtractionResult]
WIDGET_EXTRACTORS: dict[str, WidgetExtractor] = {}


def register_widget(platform: str, fn: WidgetExtractor) -> None:
    WIDGET_EXTRACTORS[platform] = fn


def _cell_from_text(raw: str, col, prayer: Prayer, d: date) -> Cell | None:
    """Build one Cell from a raw table value for a column whose prayer is already
    resolved (from the header in column layout, or a row label in row layout).
    Honours value_kind="offset". Returns None when the text holds no usable value."""
    if col.value_kind == "offset":
        off = parse_offset(raw)
        if off is None:
            return None
        return Cell(
            date=d, prayer=prayer, kind=col.kind, time=None,
            header_seen=col.header_seen, offset_min=off,
            base_prayer=col.base_prayer or prayer,
        )
    t = parse_time(raw, prefer_pm=_prefer_pm(prayer))
    if t is None:
        return None
    return Cell(date=d, prayer=prayer, kind=col.kind, time=t, header_seen=col.header_seen)


def _extract_vertical(matrix, grid, run_day: date) -> ExtractionResult:
    """Prayer-rows layout: each body row's label column names the prayer; the
    configured columns name the kinds. Rows whose label is not a daily prayer
    (a header row, a Sunrise row) are skipped. All cells are dated run_day."""
    result = ExtractionResult()
    label_idx = grid.prayer_label_index
    for texts in matrix:
        result.texts.extend(texts)
        if label_idx >= len(texts):
            continue
        match = resolve_prayer(texts[label_idx])
        prayer = match.prayer
        if prayer is None or match.fuzzy or prayer not in DAILY_PRAYERS:
            continue
        for col in grid.columns:
            if col.index is None or col.index >= len(texts):
                continue
            cell = _cell_from_text(texts[col.index], col, prayer, run_day)
            if cell is not None:
                result.cells.append(cell)
    return result


def _emit_row(
    texts, grid, date_idx, *, year: int, month: int | None, result: ExtractionResult
) -> None:
    """Emit cells for one body row of a column layout: resolve the date from
    ``date_idx`` against ``(year, month)``, then read each configured prayer
    column. A row whose date cell does not parse (a header or month-caption row)
    yields nothing. Shared by the default multi-day path and month-section path."""
    if date_idx is None or date_idx >= len(texts):
        return
    d = parse_date(texts[date_idx], year=year, month=month)
    if d is None:
        return
    for col in grid.columns:
        if col.index is None or col.index >= len(texts) or col.prayer is None:
            continue
        cell = _cell_from_text(texts[col.index], col, col.prayer, d)
        if cell is not None:
            result.cells.append(cell)


def _year_for_month(run_day: date, month: int) -> int:
    """The year a perpetual annual timetable's month belongs to relative to the
    run day: the current year for the run month onward, next year for a month
    already past (it wraps). Over-produced far-future days are filtered by the
    horizon at materialize."""
    return run_day.year if month >= run_day.month else run_day.year + 1


def _extract_month_sections(soup, grid, run_day: date) -> ExtractionResult:
    """Annual page where day-only rows are scoped by a month caption — one table
    per month, or full-width month rows within one table. Each table's month is
    seeded from its ``<caption>`` and updated by any full-width month row; data
    rows beneath a month are dated (that month's year via ``_year_for_month``).
    Rows before any month caption are skipped rather than guessed."""
    result = ExtractionResult()
    date_idx = grid.date.index if grid.date else None
    tables = soup.select(grid.table_selector) if grid.table_selector else soup.find_all("table")
    for table in tables:
        matrix = grid_matrix(table)
        if grid.transpose:
            matrix = [list(row) for row in zip(*matrix, strict=False)]
        current_month = caption_month(table)
        for texts in matrix:
            result.texts.extend(texts)
            section_month = row_month(texts)
            if section_month is not None:
                current_month = section_month
                continue
            if current_month is None:
                continue
            _emit_row(
                texts, grid, date_idx,
                year=_year_for_month(run_day, current_month), month=current_month,
                result=result,
            )
    return result


def _extract_single_day(matrix, grid, run_day: date) -> ExtractionResult:
    """Horizontal layout with no date axis: prayers are in the header, the first
    body row that yields times is today's snapshot. All cells are dated run_day."""
    result = ExtractionResult()
    for texts in matrix:
        result.texts.extend(texts)
        emitted = False
        for col in grid.columns:
            if col.index is None or col.index >= len(texts) or col.prayer is None:
                continue
            cell = _cell_from_text(texts[col.index], col, col.prayer, run_day)
            if cell is not None:
                result.cells.append(cell)
                emitted = True
        if emitted:
            break  # one data row is the whole timetable; ignore any stray rows
    return result


def extract_html_table(
    html: str,
    config: SourceConfig,
    *,
    year: int,
    month: int | None = None,
    today: date | None = None,
) -> ExtractionResult:
    grid = config.grid
    soup = BeautifulSoup(html, "lxml")
    run_day = today or date.today()

    # Month-section layout spans many tables / in-table sections, so it selects
    # its own tables rather than a single one.
    if grid.month_sections:
        return _extract_month_sections(soup, grid, run_day)

    table = soup.select_one(grid.table_selector) if grid.table_selector else soup.find("table")
    if table is None:
        return ExtractionResult(warnings=["table not found"])

    # Shared grid model so body indices match the detector's column indices even
    # when a row uses colspan/rowspan.
    matrix = grid_matrix(table)
    if grid.transpose:
        matrix = [list(row) for row in zip(*matrix, strict=False)]

    if grid.prayer_label_index is not None:
        return _extract_vertical(matrix, grid, run_day)
    if grid.single_day:
        return _extract_single_day(matrix, grid, run_day)

    result = ExtractionResult()
    date_idx = grid.date.index if grid.date else None
    for texts in matrix:
        result.texts.extend(texts)
        _emit_row(texts, grid, date_idx, year=year, month=month, result=result)
    return result


def extract_html_repeated(
    html: str, config: SourceConfig, *, year: int, month: int | None = None,
    today: date | None = None,
) -> ExtractionResult:
    grid = config.grid
    soup = BeautifulSoup(html, "lxml")
    items = soup.select(grid.row_selector) if grid.row_selector else []
    if not items:
        return ExtractionResult(warnings=["no rows matched row_selector"])

    result = ExtractionResult()
    for item in items:
        dtext = None
        if grid.date and grid.date.selector:
            el = item.select_one(grid.date.selector)
            dtext = el.get_text(" ", strip=True) if el else None
        if dtext:
            result.texts.append(dtext)
        d = parse_date(dtext, year=year, month=month) if dtext else None
        if d is None:
            continue
        for col in grid.columns:
            if not col.selector or col.prayer is None:
                continue
            el = item.select_one(col.selector)
            if el is None:
                continue
            raw = el.get_text(" ", strip=True)
            result.texts.append(raw)
            t = parse_time(raw, prefer_pm=_prefer_pm(col.prayer))
            if t is None:
                continue
            result.cells.append(
                Cell(date=d, prayer=col.prayer, kind=col.kind, time=t, header_seen=col.header_seen)
            )
    return result


def _extract_rules(
    html: str, config: SourceConfig, *, year: int, month: int | None = None,
    today: date | None = None,
) -> ExtractionResult:
    # "rules" yields no scraped cells; times are produced later by materialize_rules.
    return ExtractionResult()


def _extract_widget(
    html: str, config: SourceConfig, *, year: int, month: int | None = None,
    today: date | None = None,
) -> ExtractionResult:
    platform = config.widget.platform
    fn = WIDGET_EXTRACTORS.get(platform)
    if fn is None:
        raise ValueError(f"no widget extractor for platform: {platform!r}")
    return fn(html, year=year, month=month)


def _extract_bespoke(
    html: str, config: SourceConfig, *, year: int, month: int | None = None,
    today: date | None = None,
) -> ExtractionResult:
    # Local import: bespoke modules are authored against this module's Cell /
    # ExtractionResult, so the package imports engine — a top-level import here
    # would be circular.
    from directory.ingest.extractors.bespoke import get_bespoke

    key = config.bespoke.module
    fn = get_bespoke(key)
    if fn is None:
        raise ValueError(f"no bespoke extractor for module: {key!r}")
    try:
        return fn(html, year=year, month=month)
    except Exception as exc:
        return ExtractionResult(warnings=[f"bespoke extractor {key!r} raised: {exc}"])


# The single place that answers "what can the engine extract, and how is each
# shape resolved". Widget/bespoke seams live behind their helper, not inline.
_SHAPE_EXTRACTORS: dict[str, Callable[..., ExtractionResult]] = {
    "html_table": extract_html_table,
    "html_repeated": extract_html_repeated,
    "rules": _extract_rules,
    "widget": _extract_widget,
    "bespoke": _extract_bespoke,
}


def extract(
    html: str, config: SourceConfig, *, year: int, month: int | None = None,
    today: date | None = None,
) -> ExtractionResult:
    handler = _SHAPE_EXTRACTORS.get(config.shape)
    if handler is None:
        raise ValueError(f"unsupported shape: {config.shape!r}")
    return handler(html, config, year=year, month=month, today=today)
