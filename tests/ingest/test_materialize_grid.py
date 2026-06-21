from datetime import date

from directory.domain import Prayer
from directory.ingest.extractors.engine import Cell
from directory.ingest.materialize import OccurrenceRow, materialize_grid


def test_merges_begin_and_jamaah_per_date_prayer():
    cells = [
        Cell(date(2026, 6, 21), Prayer.FAJR, "begin", "04:45"),
        Cell(date(2026, 6, 21), Prayer.FAJR, "jamaah", "05:00"),
        Cell(date(2026, 6, 21), Prayer.DHUHR, "jamaah", "13:30"),
    ]
    rows = materialize_grid(cells, horizon_start=date(2026, 6, 1), horizon_end=date(2026, 6, 30))
    assert OccurrenceRow("2026-06-21", "fajr", 0, "05:00", "04:45", None) in rows
    assert OccurrenceRow("2026-06-21", "dhuhr", 0, "13:30", None, None) in rows


def test_begin_only_yields_no_row():
    cells = [Cell(date(2026, 6, 21), Prayer.ASR, "begin", "18:00")]
    rows = materialize_grid(
        cells, horizon_start=date(2026, 6, 1), horizon_end=date(2026, 6, 30)
    )
    assert rows == []


def test_cells_outside_horizon_dropped():
    cells = [Cell(date(2026, 5, 1), Prayer.FAJR, "jamaah", "05:00")]
    rows = materialize_grid(
        cells, horizon_start=date(2026, 6, 1), horizon_end=date(2026, 6, 30)
    )
    assert rows == []
