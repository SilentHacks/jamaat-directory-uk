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


def test_offset_jamaah_resolved_against_same_prayer_begin_is_derived():
    cells = [
        Cell(date(2026, 6, 21), Prayer.FAJR, "begin", "04:45"),
        Cell(date(2026, 6, 21), Prayer.FAJR, "jamaah", None,
             offset_min=5, base_prayer=Prayer.FAJR),
    ]
    rows = materialize_grid(cells, horizon_start=date(2026, 6, 1), horizon_end=date(2026, 6, 30))
    assert OccurrenceRow("2026-06-21", "fajr", 0, "04:50", "04:45", None, derived=True) in rows


def test_cross_prayer_offset_base_resolves():
    cells = [
        Cell(date(2026, 6, 21), Prayer.MAGHRIB, "begin", "21:30"),
        Cell(date(2026, 6, 21), Prayer.ISHA, "jamaah", None,
             offset_min=90, base_prayer=Prayer.MAGHRIB),
    ]
    rows = materialize_grid(cells, horizon_start=date(2026, 6, 1), horizon_end=date(2026, 6, 30))
    isha = next(r for r in rows if r.prayer == "isha")
    assert isha.jamaah_time == "23:00"
    assert isha.derived is True


def test_offset_without_a_begin_yields_no_row():
    cells = [
        Cell(date(2026, 6, 21), Prayer.FAJR, "jamaah", None,
             offset_min=5, base_prayer=Prayer.FAJR),
    ]
    rows = materialize_grid(cells, horizon_start=date(2026, 6, 1), horizon_end=date(2026, 6, 30))
    assert rows == []
