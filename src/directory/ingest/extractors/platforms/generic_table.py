from datetime import date

from bs4 import BeautifulSoup

from directory.domain import DAILY_PRAYERS, Prayer
from directory.ingest.extractors.config_schema import (
    ColumnSpec,
    DateSpec,
    GridSpec,
    SourceConfig,
)
from directory.ingest.extractors.platforms.base import PlatformMatch
from directory.ingest.extractors.tablegrid import (
    combined_header,
    grid_matrix,
    header_depth,
)
from directory.ingest.normalize import (
    normalize_token,
    parse_date,
    parse_offset,
    parse_time,
    resolve_kind,
    resolve_prayer,
)

_SAMPLE_ROWS = 12
_MIN_FRACTION = 0.5
_MIN_PRAYER_COLS = 3


def _table_selector(table) -> str | None:
    if table.get("id"):
        return f"table#{table.get('id')}"
    classes = table.get("class") or []
    if classes:
        return "table." + classes[0]
    return None


def _column(body: list[list[str]], idx: int) -> list[str]:
    return [row[idx] for row in body[:_SAMPLE_ROWS] if idx < len(row)]


def _fraction(cells: list[str], ok) -> float:
    if not cells:
        return 0.0
    return sum(1 for c in cells if ok(c)) / len(cells)


def _parses_date(cell: str) -> bool:
    today = date.today()
    return parse_date(cell, year=today.year, month=today.month) is not None


def _time_or_offset(cells: list[str]) -> tuple[float, float, bool]:
    """Classify a column's body: (time_frac, offset_frac, looks_like_offset).
    A column is an offset column when its cells parse as "+N" at least as often
    as they parse as clock times. Shared by the column- and row-layout detectors."""
    time_frac = _fraction(cells, lambda c: parse_time(c) is not None)
    offset_frac = _fraction(cells, lambda c: parse_offset(c) is not None)
    is_offset = offset_frac >= _MIN_FRACTION and offset_frac >= time_frac
    return time_frac, offset_frac, is_offset


def _daily_label(text: str) -> Prayer | None:
    """The daily prayer a row/header label names exactly (non-fuzzy), else None."""
    match = resolve_prayer(text)
    if match.prayer in DAILY_PRAYERS and not match.fuzzy:
        return match.prayer
    return None


def _detect_columns(header: list[str], body: list[list[str]]) -> list[ColumnSpec]:
    # First pass: resolve each prayer column and whether its body is times or
    # "+N" offsets. Second pass drops offset columns that have no begin column to
    # resolve against, since they cannot be materialized.
    raw: list[tuple[int, Prayer, str, str, bool]] = []  # (idx, prayer, kind, text, is_offset)
    for idx, text in enumerate(header):
        match = resolve_prayer(text)
        prayer = match.prayer
        if prayer is None or prayer not in DAILY_PRAYERS:
            continue
        cells = _column(body, idx)
        time_frac, offset_frac, looks_offset = _time_or_offset(cells)
        # Fuzzy header matches are only trusted when the body looks like times or offsets.
        if match.fuzzy and max(time_frac, offset_frac) < _MIN_FRACTION:
            continue
        kind = resolve_kind(text).kind or "jamaah"
        is_offset = kind == "jamaah" and looks_offset
        raw.append((idx, prayer, kind, text, is_offset))

    begin_prayers = {p for (_, p, k, _, _) in raw if k == "begin"}
    columns: list[ColumnSpec] = []
    seen: set[tuple] = set()
    for idx, prayer, kind, text, is_offset in raw:
        if is_offset and prayer not in begin_prayers:
            continue  # "+N" with no begin time → unmaterializable
        key = (prayer, kind)
        if key in seen:
            continue
        seen.add(key)
        columns.append(
            ColumnSpec(
                kind=kind, prayer=prayer, index=idx, header_seen=text,
                value_kind="offset" if is_offset else None,
            )
        )
    return columns


def _detect_date_index(header: list[str], body: list[list[str]], used: set[int]) -> int | None:
    best_idx, best_frac = None, 0.0
    width = max((len(r) for r in body), default=0)
    for idx in range(width):
        if idx in used:
            continue
        frac = _fraction(_column(body, idx), _parses_date)
        if frac > best_frac:
            best_idx, best_frac = idx, frac
    if best_idx is not None and best_frac >= _MIN_FRACTION:
        return best_idx
    # Fallback: a header that names a date/day column.
    for idx, text in enumerate(header):
        if idx in used:
            continue
        if normalize_token(text) in {"date", "day"}:
            return idx
    return None


def _detect_vertical(
    header: list[str], body: list[list[str]]
) -> tuple[int, list[ColumnSpec]] | None:
    """Prayer-rows layout: find the column whose body cells name daily prayers
    (the label column), then read the remaining columns as kinds from the header.
    Returns (label_index, kind_columns) or None."""
    width = max((len(r) for r in body), default=0)
    label_idx, best = None, 0
    for idx in range(width):
        prayers = {p for c in _column(body, idx) if (p := _daily_label(c)) is not None}
        if len(prayers) > best:
            label_idx, best = idx, len(prayers)
    if label_idx is None or best < _MIN_PRAYER_COLS:
        return None

    # Only rows that name a daily prayer carry timetable data; a Sunrise row (and
    # its non-time cells) must not drag a kind column's time fraction down.
    labeled = [r for r in body if label_idx < len(r) and _daily_label(r[label_idx])]
    columns: list[ColumnSpec] = []
    for idx in range(width):
        if idx == label_idx:
            continue
        cells = [r[idx] for r in labeled[:_SAMPLE_ROWS] if idx < len(r)]
        _time_frac, _offset_frac, is_offset = _time_or_offset(cells)
        if max(_time_frac, _offset_frac) < _MIN_FRACTION:
            continue
        htext = header[idx] if idx < len(header) else ""
        kind = resolve_kind(htext).kind or "jamaah"
        if kind != "jamaah":
            is_offset = False  # only a jamaah column can be a "+N" offset
        columns.append(
            ColumnSpec(
                kind=kind, prayer=None, index=idx, header_seen=htext or None,
                value_kind="offset" if is_offset else None,
            )
        )
    if not columns:
        return None
    return label_idx, columns


class GenericTableDetector:
    name = "generic_table"

    def detect(self, html: str, url: str) -> PlatformMatch | None:
        soup = BeautifulSoup(html, "lxml")
        for table in soup.find_all("table"):
            grid = grid_matrix(table)
            depth = header_depth(table)
            if len(grid) <= depth:
                continue
            header = combined_header(grid, depth)
            body = grid[depth:]
            # Richest layout first: multi-day (more horizon coverage) beats a
            # single-day snapshot; a recognised orientation beats none.
            config = (
                _horizontal_multiday(table, header, body)
                or _transpose_multiday(table, grid)
                or _horizontal_single_day(table, header, body)
                or _vertical_single_day(table, header, body)
            )
            if config is not None:
                return PlatformMatch(
                    platform=self.name, url=url, requires_js=False, config=config
                )
        return None


def _horizontal_multiday(table, header, body) -> SourceConfig | None:
    """The classic layout: prayers across the header, one date per body row."""
    columns = _detect_columns(header, body)
    if len(columns) < _MIN_PRAYER_COLS:
        return None
    date_idx = _detect_date_index(header, body, {c.index for c in columns})
    if date_idx is None:
        return None
    return SourceConfig(
        shape="html_table",
        grid=GridSpec(
            table_selector=_table_selector(table),
            transpose=False,
            date=DateSpec(index=date_idx),
            columns=columns,
        ),
    )


def _transpose_multiday(table, grid) -> SourceConfig | None:
    """Prayers down the side, dates across the top: flip the grid and reuse the
    column/date detectors. The engine applies the same flip via transpose=True."""
    if len(grid) < 2:
        return None
    tgrid = [list(row) for row in zip(*grid, strict=False)]
    if len(tgrid) < 2:
        return None
    theader, tbody = tgrid[0], tgrid[1:]
    columns = _detect_columns(theader, tbody)
    if len(columns) < _MIN_PRAYER_COLS:
        return None
    date_idx = _detect_date_index(theader, tbody, {c.index for c in columns})
    if date_idx is None:
        return None
    return SourceConfig(
        shape="html_table",
        grid=GridSpec(
            table_selector=_table_selector(table),
            transpose=True,
            date=DateSpec(index=date_idx),
            columns=columns,
        ),
    )


def _horizontal_single_day(table, header, body) -> SourceConfig | None:
    """Prayers across the header but no date axis — a single 'today' row."""
    columns = _detect_columns(header, body)
    if len(columns) < _MIN_PRAYER_COLS:
        return None
    if _detect_date_index(header, body, {c.index for c in columns}) is not None:
        return None  # a date column means multi-day, handled earlier
    data_rows = [
        r for r in body
        if any(c.index is not None and c.index < len(r) and parse_time(r[c.index]) for c in columns)
    ]
    if len(data_rows) != 1:
        return None  # >1 timed row with no date is ambiguous → leave to the AI tier
    return SourceConfig(
        shape="html_table",
        grid=GridSpec(
            table_selector=_table_selector(table),
            single_day=True,
            columns=columns,
        ),
    )


def _vertical_single_day(table, header, body) -> SourceConfig | None:
    """Prayers down a label column, kinds across the header, no date axis."""
    found = _detect_vertical(header, body)
    if found is None:
        return None
    label_idx, columns = found
    return SourceConfig(
        shape="html_table",
        grid=GridSpec(
            table_selector=_table_selector(table),
            prayer_label_index=label_idx,
            single_day=True,
            columns=columns,
        ),
    )
