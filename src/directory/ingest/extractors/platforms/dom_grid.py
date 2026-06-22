"""Detector for div-based "tables" (see ``extractors/dom_grid`` for the matrix
builder). It synthesises the matrix, runs the shared generic-table layout
detectors on it, and marks the resulting config ``dom_grid`` so the engine
rebuilds the matrix the same way at extract time. A match always requires JS,
since these grids only exist after rendering.
"""

from directory.ingest.extractors.config_schema import SourceConfig
from directory.ingest.extractors.dom_grid import dom_matrix
from directory.ingest.extractors.platforms.base import PlatformMatch
from directory.ingest.extractors.platforms.generic_table import (
    _horizontal_multiday,
    _horizontal_single_day,
    _transpose_multiday,
    _vertical_single_day,
)
from directory.ingest.extractors.tablegrid import combined_header, content_header_depth

_MIN_ROWS = 2


class DomGridDetector:
    name = "dom_grid"

    def detect(self, html: str, url: str) -> PlatformMatch | None:
        matrix = dom_matrix(html)
        if matrix is None or len(matrix) < _MIN_ROWS:
            return None
        depth = content_header_depth(matrix) or 1
        if len(matrix) <= depth:
            return None
        header = combined_header(matrix, depth)
        body = matrix[depth:]
        # selector=None: the engine re-derives the matrix via dom_matrix each run.
        config = (
            _horizontal_multiday(None, header, body)
            or _transpose_multiday(None, matrix)
            or _horizontal_single_day(None, header, body)
            or _vertical_single_day(None, header, body)
        )
        if config is None:
            return None
        config.grid.dom_grid = True
        return PlatformMatch(platform=self.name, url=url, requires_js=True, config=config)
