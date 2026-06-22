"""Shared HTML-table column/date authoring.

Given a table's collapsed ``header`` (one label per logical column) and ``body``
(text rows), these helpers decide which columns are prayers (and whether each is
a Begin/Jamā‘ah/offset column) and which column carries the date. They are used
by the ``generic_table`` detector and by the endpoint detectors (DPT, the generic
admin-ajax sniffer), so a month grid fetched from a data endpoint is authored by
exactly the same logic as one found inline.

``header_body`` adds one capability over ``generic_table``'s inline path: it
reconstructs grouped headers whose ``<th>`` cells are not ``<tr>``-wrapped (see
``tablegrid.bare_thead_rows``), so plugin tables that emit ``<thead><th>…`` are
authored correctly rather than mistaking the first data row for the header.
"""

from datetime import date

from directory.domain import DAILY_PRAYERS, Prayer
from directory.ingest.extractors.config_schema import ColumnSpec, DateSpec, GridSpec
from directory.ingest.extractors.tablegrid import (
    bare_thead_rows,
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


def detect_columns(header: list[str], body: list[list[str]]) -> list[ColumnSpec]:
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


def detect_date_index(header: list[str], body: list[list[str]], used: set[int]) -> int | None:
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


def detect_vertical(
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


def header_body(table) -> tuple[list[str], list[list[str]]] | None:
    """Collapse a ``<table>`` into ``(header, body)``: one combined label per
    logical column and the text rows beneath the header. Handles both ordinary
    ``<tr>``-wrapped headers and grouped headers whose ``<th>`` are bare under a
    ``<thead>`` (``tablegrid.bare_thead_rows``). Returns None when no body remains."""
    bare = bare_thead_rows(table)
    if bare:
        body = grid_matrix(table)  # bare-th theads have no <tr>, so this is the body
        if not body:
            return None
        width = max(len(r) for r in (*bare, *body))
        padded = [r + [""] * (width - len(r)) for r in bare]
        return combined_header(padded, len(padded)), body
    grid = grid_matrix(table)
    depth = header_depth(table)
    if len(grid) <= depth:
        return None
    return combined_header(grid, depth), grid[depth:]


def author_grid(table, selector: str | None) -> GridSpec | None:
    """Author a multi-day ``html_table`` ``GridSpec`` from a single month table:
    detect the prayer columns and the date column. Returns None when the table is
    not a recognisable multi-day prayer grid (too few prayers, or no date axis)."""
    hb = header_body(table)
    if hb is None:
        return None
    header, body = hb
    columns = detect_columns(header, body)
    if len(columns) < _MIN_PRAYER_COLS:
        return None
    date_idx = detect_date_index(header, body, {c.index for c in columns})
    if date_idx is None:
        return None
    return GridSpec(
        table_selector=selector,
        transpose=False,
        date=DateSpec(index=date_idx),
        columns=columns,
    )
