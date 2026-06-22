"""Virtual-matrix builder for div-based "tables".

Some sites render a perfectly tabular timetable — a header row of prayer names
plus one row per day — but with ``<div>``s (ARIA ``role="table"`` grids, or
repeated equal-width sibling rows) instead of ``<table>`` markup. ``dom_matrix``
synthesises the same dense ``list[list[str]]`` that ``grid_matrix`` builds from a
real table, so the existing generic-table detectors and the table engine run on
it unchanged. It is re-derived from the live DOM on every run (content/geometry,
never a stored hashed selector), so a ``dom_grid`` config carries no selector.

This module is import-light (bs4 + normalize only) so the engine can pull it in
without cycling through the platforms package; the detector lives in
``platforms/dom_grid.py``.
"""

from bs4 import BeautifulSoup, Tag

from directory.ingest.normalize import parse_time

_CELL_ROLES = frozenset({"cell", "gridcell", "columnheader", "rowheader"})
_MIN_ROWS = 2
_MIN_COLS = 2
_MIN_TIME_CELLS = 5  # a real timetable grid carries many clock values


def _pad(matrix: list[list[str]]) -> list[list[str]]:
    width = max((len(r) for r in matrix), default=0)
    return [r + [""] * (width - len(r)) for r in matrix]


def _time_cells(matrix: list[list[str]]) -> int:
    return sum(1 for row in matrix for cell in row if parse_time(cell, prefer_pm=True))


def _aria_matrix(soup: BeautifulSoup) -> list[list[str]] | None:
    rows = soup.find_all(attrs={"role": "row"})
    if len(rows) < _MIN_ROWS:
        return None
    matrix: list[list[str]] = []
    for row in rows:
        cells = [
            c for c in row.find_all(attrs={"role": True})
            if c.get("role") in _CELL_ROLES
        ]
        if cells:
            matrix.append([c.get_text(" ", strip=True) for c in cells])
    if len(matrix) < _MIN_ROWS or max((len(r) for r in matrix), default=0) < _MIN_COLS:
        return None
    return _pad(matrix)


def _repeated_rows_matrix(soup: BeautifulSoup) -> list[list[str]] | None:
    """Induce a grid from a container of equal-width sibling 'row' elements, each
    split into its direct child cells. Among all candidates, the timetable is the
    one carrying the most clock values."""
    best: list[list[str]] | None = None
    best_times = 0
    for parent in soup.find_all(True):
        children = [c for c in parent.find_all(recursive=False) if isinstance(c, Tag)]
        if len(children) < 3:
            continue
        matrix: list[list[str]] = []
        widths: set[int] = set()
        ok = True
        for child in children:
            cells = [
                g for g in child.find_all(recursive=False)
                if isinstance(g, Tag) and g.get_text(strip=True)
            ]
            if len(cells) < _MIN_COLS:
                ok = False
                break
            matrix.append([g.get_text(" ", strip=True) for g in cells])
            widths.add(len(cells))
        if not ok or len(widths) != 1:  # uniform width = a real grid, not a menu
            continue
        times = _time_cells(matrix)
        if times >= _MIN_TIME_CELLS and times > best_times:
            best, best_times = matrix, times
    return _pad(best) if best else None


def dom_matrix(html_or_soup) -> list[list[str]] | None:
    """The dense text matrix of a div-based grid, or None when the page has none.
    ARIA role grids are preferred; otherwise a repeated equal-width sibling-row
    container is induced."""
    soup = (
        html_or_soup
        if isinstance(html_or_soup, BeautifulSoup)
        else BeautifulSoup(html_or_soup, "lxml")
    )
    return _aria_matrix(soup) or _repeated_rows_matrix(soup)
