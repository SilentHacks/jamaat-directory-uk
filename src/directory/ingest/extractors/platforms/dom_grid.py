"""Detector for div-based "tables" (see ``extractors/dom_grid`` for the matrix
builder). It synthesises the matrix, runs the shared generic-table layout
detectors on it, and marks the resulting config ``dom_grid`` so the engine
rebuilds the matrix the same way at extract time. A match always requires JS,
since these grids only exist after rendering.
"""

from directory.domain import DAILY_PRAYERS
from directory.ingest.extractors.dom_grid import dom_matrix
from directory.ingest.extractors.platforms.base import PlatformMatch
from directory.ingest.extractors.platforms.generic_table import (
    horizontal_multiday,
    horizontal_single_day,
    transpose_multiday,
    vertical_single_day,
)
from directory.ingest.extractors.tablegrid import combined_header, content_header_depth
from directory.ingest.normalize import resolve_prayer

_MIN_ROWS = 2


def _body_prayer_columns(body: list[list[str]]) -> int:
    """How many distinct columns carry a daily-prayer name in the body. A true
    grid has prayer names only in its header (0) or in a single vertical label
    column (1); a per-day *record card* gridded by mistake scatters them across
    many columns (>1), which is the signal to leave it to the record-stream
    extractor instead."""
    cols: set[int] = set()
    for row in body:
        for idx, cell in enumerate(row):
            match = resolve_prayer(cell)
            if match.prayer in DAILY_PRAYERS and not match.fuzzy:
                cols.add(idx)
    return len(cols)


class DomGridDetector:
    name = "dom_grid"

    def detect(self, html: str, url: str, *, fetcher=None) -> PlatformMatch | None:
        matrix = dom_matrix(html)
        if matrix is None or len(matrix) < _MIN_ROWS:
            return None
        depth = content_header_depth(matrix) or 1
        if len(matrix) <= depth:
            return None
        header = combined_header(matrix, depth)
        body = matrix[depth:]
        if _body_prayer_columns(body) > 1:
            return None  # a per-day record card, not a grid → leave to dom_records
        # selector=None: the engine re-derives the matrix via dom_matrix each run.
        config = (
            horizontal_multiday(None, header, body)
            or transpose_multiday(None, matrix)
            or horizontal_single_day(None, header, body)
            or vertical_single_day(None, header, body)
        )
        if config is None:
            return None
        config.grid.dom_grid = True
        return PlatformMatch(platform=self.name, url=url, requires_js=True, config=config)
