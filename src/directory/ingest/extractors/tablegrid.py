"""Shared HTML-table geometry.

The detector and the engine must agree on column indices. A header that uses
``colspan``/``rowspan`` makes raw-cell order diverge from logical-column order,
so both modules build their matrix here — one grid model, one source of truth.
"""

from directory.ingest.normalize import month_from_text, parse_time


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


def bare_thead_rows(table) -> list[list[str]]:
    """Header rows from ``<thead>`` elements whose ``<th>``/``<td>`` cells sit
    *directly* under the thead with no ``<tr>`` wrapper — colspan-expanded so they
    align with the body matrix. Some plugins (e.g. the Divi "Daily Prayer Time"
    monthly table) emit grouped headers this way, which the ``<tr>``-based
    ``grid_matrix`` cannot see. Returns one expanded list per such thead; empty
    when every thead is normally ``<tr>``-wrapped (the common case)."""
    rows: list[list[str]] = []
    for thead in table.find_all("thead"):
        if thead.find("tr") is not None:
            continue  # a normal <tr> header — grid_matrix/header_depth handle it
        cells = thead.find_all(["th", "td"], recursive=False)
        if not cells:
            continue
        row: list[str] = []
        for cell in cells:
            text = cell.get_text(" ", strip=True)
            row.extend([text] * _int(cell.get("colspan"), 1))
        rows.append(row)
    return rows


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
    rows = [
        [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
        for tr in table.find_all("tr")
    ]
    return content_header_depth(rows) or 1


def content_header_depth(rows: list[list[str]]) -> int:
    """The leading run of time-less rows in a text matrix — the header rows. May
    be 0 when the first row already holds a clock time (no header). Used to split
    a month section's rows into header + body."""
    depth = 0
    for row in rows:
        if any(parse_time(c) for c in row):
            break
        depth += 1
    return depth


def caption_month(table) -> int | None:
    """The month a table's ``<caption>`` element names, if any."""
    cap = table.find("caption")
    return month_from_text(cap.get_text(" ", strip=True)) if cap else None


def row_month(row: list[str]) -> int | None:
    """The month a full-width section row names — every non-empty cell is the same
    month label (e.g. a colspan ``February`` row) — else None."""
    distinct = {t for t in row if t.strip()}
    if len(distinct) != 1:
        return None
    return month_from_text(next(iter(distinct)))


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
