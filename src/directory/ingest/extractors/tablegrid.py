"""Shared HTML-table geometry.

The detector and the engine must agree on column indices. A header that uses
``colspan``/``rowspan`` makes raw-cell order diverge from logical-column order,
so both modules build their matrix here — one grid model, one source of truth.
"""

from directory.ingest.normalize import parse_time


def _int(value, default: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(n, 1)


def grid_matrix(table) -> list[list[str]]:
    """Flatten ``<tr>`` rows into a dense rectangular matrix, expanding
    ``colspan`` (text repeated across spanned columns) and ``rowspan`` (text
    carried down into spanned rows). A flat, span-free table returns exactly the
    naive ``find_all(["td","th"])`` flattening."""
    rows = table.find_all("tr")
    occupied: dict[tuple[int, int], str] = {}
    width = 0
    for r, tr in enumerate(rows):
        c = 0
        for cell in tr.find_all(["td", "th"]):
            while (r, c) in occupied:  # skip columns held by a rowspan from above
                c += 1
            text = cell.get_text(" ", strip=True)
            cspan = _int(cell.get("colspan"), 1)
            rspan = _int(cell.get("rowspan"), 1)
            for dr in range(rspan):
                for dc in range(cspan):
                    occupied[(r + dr, c + dc)] = text
            c += cspan
            width = max(width, c)
    return [[occupied.get((r, c), "") for c in range(width)] for r in range(len(rows))]


def header_depth(table) -> int:
    """Number of leading header rows: ``<thead>`` row count if present, else the
    run of leading ``<tr>``s whose cells are all ``<th>``, else inferred from
    content. Always at least 1."""
    thead = table.find("thead")
    if thead is not None:
        n = len(thead.find_all("tr"))
        if n:
            return n
    depth = 0
    for tr in table.find_all("tr"):
        cells = tr.find_all(["td", "th"])
        if cells and all(c.name == "th" for c in cells):
            depth += 1
        else:
            break
    return depth or _content_header_depth(table)


def _content_header_depth(table) -> int:
    """Infer the header of a table with no ``<thead>``/``<th>`` markup: a header
    row carries no parseable clock time (a month caption, a prayer-name row, a
    Begins/Jamā‘ah row), and the first timed row begins the body. One stray time
    per row is enough to call it data, so a single-time-column vertical layout
    keeps a depth of 1 rather than swallowing every row. Always at least 1."""
    depth = 0
    for tr in table.find_all("tr"):
        cells = tr.find_all(["td", "th"])
        if any(parse_time(c.get_text(" ", strip=True)) for c in cells):
            break
        depth += 1
    return depth or 1


def combined_header(grid: list[list[str]], depth: int) -> list[str]:
    """Collapse the top ``depth`` header rows into one label per logical column,
    joining each column's non-empty header texts top-to-bottom (e.g.
    ``"Fajr Begins"``). Consecutive identical texts (a rowspan carry) collapse to
    one, so a ``Date`` cell spanning both header rows stays ``"Date"``."""
    header_rows = grid[:depth]
    width = max((len(r) for r in grid), default=0)
    out: list[str] = []
    for c in range(width):
        parts: list[str] = []
        for hr in header_rows:
            text = hr[c] if c < len(hr) else ""
            if text and (not parts or parts[-1] != text):
                parts.append(text)
        out.append(" ".join(parts))
    return out
