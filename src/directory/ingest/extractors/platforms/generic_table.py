from datetime import date

from bs4 import BeautifulSoup

from directory.domain import DAILY_PRAYERS
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


def _detect_columns(header: list[str], body: list[list[str]]) -> list[ColumnSpec]:
    columns: list[ColumnSpec] = []
    seen: set[tuple] = set()
    for idx, text in enumerate(header):
        match = resolve_prayer(text)
        prayer = match.prayer
        if prayer is None or prayer not in DAILY_PRAYERS:
            continue
        # Fuzzy header matches are only trusted when the column body looks like times.
        if match.fuzzy:
            time_frac = _fraction(_column(body, idx), lambda c: parse_time(c) is not None)
            if time_frac < _MIN_FRACTION:
                continue
        kind = resolve_kind(text).kind or "jamaah"
        key = (prayer, kind)
        if key in seen:
            continue
        seen.add(key)
        columns.append(ColumnSpec(kind=kind, prayer=prayer, index=idx, header_seen=text))
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
            columns = _detect_columns(header, body)
            if len(columns) < _MIN_PRAYER_COLS:
                continue
            used = {c.index for c in columns}
            date_idx = _detect_date_index(header, body, used)
            if date_idx is None:
                continue
            config = SourceConfig(
                shape="html_table",
                grid=GridSpec(
                    table_selector=_table_selector(table),
                    transpose=False,
                    date=DateSpec(index=date_idx),
                    columns=columns,
                ),
            )
            return PlatformMatch(
                platform=self.name, url=url, requires_js=False, config=config
            )
        return None
