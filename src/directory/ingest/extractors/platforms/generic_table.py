from bs4 import BeautifulSoup

from directory.ingest.extractors.column_author import (
    _MIN_PRAYER_COLS,
    detect_columns,
    detect_date_index,
    detect_vertical,
)
from directory.ingest.extractors.config_schema import (
    DateSpec,
    GridSpec,
    SourceConfig,
)
from directory.ingest.extractors.platforms.base import PlatformMatch
from directory.ingest.extractors.table_orientations import (
    HORIZONTAL_MULTIDAY,
    HORIZONTAL_SINGLE_DAY,
    PRAYER_ROWS,
    TRANSPOSE_MULTIDAY,
    grid_for,
)
from directory.ingest.extractors.tablegrid import (
    caption_month,
    combined_header,
    content_header_depth,
    grid_matrix,
    header_depth,
    row_month,
)
from directory.ingest.normalize import parse_time


def _table_selector(table) -> str | None:
    if table.get("id"):
        return f"table#{table.get('id')}"
    classes = table.get("class") or []
    if classes:
        return "table." + classes[0]
    return None


def _month_sections(soup) -> list[tuple[int, list[str], list[list[str]]]]:
    """Split the page into month sections — one per month-captioned table and per
    full-width month row within a table. Each is ``(month, header, body)`` with
    the header rows collapsed; the month-caption row itself is consumed as the
    delimiter, so headers stay clean. Sections without a header are skipped."""
    sections: list[tuple[int, list[str], list[list[str]]]] = []
    for table in soup.find_all("table"):
        grid = grid_matrix(table)
        current_month = caption_month(table)
        run: list[list[str]] = []

        def _flush(month: int | None, rows: list[list[str]]) -> None:
            if month is None or not rows:
                return
            depth = content_header_depth(rows)
            if depth == 0 or depth >= len(rows):
                return  # no header, or no body rows
            sections.append((month, combined_header(rows, depth), rows[depth:]))

        for grid_row in grid:
            rm = row_month(grid_row)
            if rm is not None:
                _flush(current_month, run)
                current_month, run = rm, []
                continue
            run.append(grid_row)
        _flush(current_month, run)
    return sections


def _month_section_layout(soup) -> SourceConfig | None:
    """Annual page whose day-only rows are scoped by a month caption (separate
    per-month tables, or full-width month rows in one table). Fires only when ≥2
    distinct months are present, so an ordinary single-month titled table is left
    to the per-table path. Columns/date come from the first resolvable section."""
    sections = _month_sections(soup)
    if len({month for month, _, _ in sections}) < 2:
        return None
    for _month, header, body in sections:
        columns = detect_columns(header, body)
        if len(columns) < _MIN_PRAYER_COLS:
            continue
        date_idx = detect_date_index(header, body, {c.index for c in columns})
        if date_idx is None:
            continue
        return SourceConfig(
            shape="html_table",
            grid=GridSpec(
                month_sections=True,
                date=DateSpec(index=date_idx, format="day_only"),
                columns=columns,
            ),
        )
    return None


class GenericTableDetector:
    name = "generic_table"

    def detect(self, html: str, url: str, *, fetcher=None) -> PlatformMatch | None:
        soup = BeautifulSoup(html, "lxml")
        # Page-level shape first: an annual page of month-captioned sections spans
        # many tables, so it cannot be found by the per-table loop below.
        config = _month_section_layout(soup)
        if config is not None:
            return PlatformMatch(platform=self.name, url=url, requires_js=False, config=config)
        for table in soup.find_all("table"):
            grid = grid_matrix(table)
            depth = header_depth(table)
            if len(grid) <= depth:
                continue
            header = combined_header(grid, depth)
            body = grid[depth:]
            # Richest layout first: multi-day (more horizon coverage) beats a
            # single-day snapshot; a recognised orientation beats none.
            selector = _table_selector(table)
            config = (
                horizontal_multiday(selector, header, body)
                or transpose_multiday(selector, grid)
                or horizontal_single_day(selector, header, body)
                or vertical_single_day(selector, header, body)
            )
            if config is not None:
                return PlatformMatch(
                    platform=self.name, url=url, requires_js=False, config=config
                )
        return None


def horizontal_multiday(selector, header, body) -> SourceConfig | None:
    """The classic layout: prayers across the header, one date per body row."""
    columns = detect_columns(header, body)
    if len(columns) < _MIN_PRAYER_COLS:
        return None
    date_idx = detect_date_index(header, body, {c.index for c in columns})
    if date_idx is None:
        return None
    return SourceConfig(
        shape="html_table",
        grid=grid_for(
            HORIZONTAL_MULTIDAY, selector=selector, date_index=date_idx, columns=columns
        ),
    )


def transpose_multiday(selector, grid) -> SourceConfig | None:
    """Prayers down the side, dates across the top: flip the grid and reuse the
    column/date detectors. The engine applies the same flip via transpose=True."""
    if len(grid) < 2:
        return None
    tgrid = [list(row) for row in zip(*grid, strict=False)]
    if len(tgrid) < 2:
        return None
    theader, tbody = tgrid[0], tgrid[1:]
    columns = detect_columns(theader, tbody)
    if len(columns) < _MIN_PRAYER_COLS:
        return None
    date_idx = detect_date_index(theader, tbody, {c.index for c in columns})
    if date_idx is None:
        return None
    return SourceConfig(
        shape="html_table",
        grid=grid_for(
            TRANSPOSE_MULTIDAY, selector=selector, date_index=date_idx, columns=columns
        ),
    )


def horizontal_single_day(selector, header, body) -> SourceConfig | None:
    """Prayers across the header but no date axis — a single 'today' row."""
    columns = detect_columns(header, body)
    if len(columns) < _MIN_PRAYER_COLS:
        return None
    if detect_date_index(header, body, {c.index for c in columns}) is not None:
        return None  # a date column means multi-day, handled earlier
    data_rows = [
        r for r in body
        if any(c.index is not None and c.index < len(r) and parse_time(r[c.index]) for c in columns)
    ]
    if len(data_rows) != 1:
        return None  # >1 timed row with no date is ambiguous → leave to the AI tier
    return SourceConfig(
        shape="html_table",
        grid=grid_for(HORIZONTAL_SINGLE_DAY, selector=selector, columns=columns),
    )


def vertical_single_day(selector, header, body) -> SourceConfig | None:
    """Prayers down a label column, kinds across the header, no date axis."""
    found = detect_vertical(header, body)
    if found is None:
        return None
    label_idx, columns = found
    return SourceConfig(
        shape="html_table",
        grid=grid_for(
            PRAYER_ROWS, selector=selector, label_index=label_idx, columns=columns
        ),
    )
