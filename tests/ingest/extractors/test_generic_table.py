from directory.domain import Prayer
from directory.ingest.extractors.engine import extract
from directory.ingest.extractors.platforms.generic_table import GenericTableDetector
from directory.ingest.gates import run_gates
from directory.ingest.materialize import materialize

# A plain prayer table with no known platform CSS signature.
GENERIC_HTML = """
<html><body>
<table>
  <tr><th>Date</th><th>Fajr</th><th>Dhuhr</th><th>Asr</th><th>Maghrib</th><th>Isha</th></tr>
  <tr><td>1 June</td><td>05:00</td><td>13:15</td><td>18:30</td><td>21:10</td><td>22:30</td></tr>
  <tr><td>2 June</td><td>05:02</td><td>13:16</td><td>18:31</td><td>21:11</td><td>22:31</td></tr>
  <tr><td>3 June</td><td>05:03</td><td>13:17</td><td>18:32</td><td>21:12</td><td>22:32</td></tr>
</table>
</body></html>
"""

# A table that has a date column but only two prayer columns → not enough.
PARTIAL_HTML = """
<html><body>
<table class="pt">
  <tr><th>Date</th><th>Fajr</th><th>Dhuhr</th></tr>
  <tr><td>1 June</td><td>05:00</td><td>13:15</td></tr>
  <tr><td>2 June</td><td>05:02</td><td>13:16</td></tr>
</table>
</body></html>
"""

PLAIN_HTML = """
<html><body>
<table>
  <tr><th>Name</th><th>Phone</th><th>Email</th></tr>
  <tr><td>Imam</td><td>0123</td><td>a@b.c</td></tr>
</table>
</body></html>
"""


def test_detects_generic_table_with_class_selector():
    match = GenericTableDetector().detect(GENERIC_HTML, "https://m.example/times")
    assert match is not None
    assert match.platform == "generic_table"
    cfg = match.config
    assert cfg.shape == "html_table"
    assert cfg.grid.date.index == 0
    prayers = {c.prayer for c in cfg.grid.columns}
    assert {Prayer.FAJR, Prayer.DHUHR, Prayer.ASR, Prayer.MAGHRIB, Prayer.ISHA} <= prayers


def test_emitted_config_round_trips_through_engine():
    from datetime import date

    match = GenericTableDetector().detect(GENERIC_HTML, "https://m.example/times")
    result = extract(GENERIC_HTML, match.config, year=2026, month=6)
    rows = materialize(result, match.config,
                       horizon_start=date(2026, 6, 1), horizon_end=date(2026, 6, 30))
    gate = run_gates(match.config, result, rows, html_text=GENERIC_HTML)
    assert gate.lane == "auto_accept"
    fajr = [c for c in result.cells if c.prayer == Prayer.FAJR]
    assert {c.time for c in fajr} == {"05:00", "05:02", "05:03"}


PARTIAL_TIMETABLE_HTML = """
<html><body>
<table>
  <tr><th>Date</th><th>Fajr</th><th>Dhuhr</th><th>Asr</th></tr>
  <tr><td>1 June</td><td>05:00</td><td>13:15</td><td>18:30</td></tr>
  <tr><td>2 June</td><td>05:02</td><td>13:16</td><td>18:31</td></tr>
</table>
</body></html>
"""


def test_partial_timetable_detects_but_gates_send_to_review():
    from datetime import date

    match = GenericTableDetector().detect(PARTIAL_TIMETABLE_HTML, "https://m.example/")
    assert match is not None  # 3 prayer cols + date → detected
    result = extract(PARTIAL_TIMETABLE_HTML, match.config, year=2026, month=6)
    rows = materialize(result, match.config,
                       horizon_start=date(2026, 6, 1), horizon_end=date(2026, 6, 30))
    gate = run_gates(match.config, result, rows, html_text=PARTIAL_TIMETABLE_HTML)
    assert gate.lane == "review"  # missing maghrib + isha


def test_horizontal_multiday_not_flagged_single_day_or_vertical():
    # The classic layout must still take the first cascade branch unchanged: a
    # real date axis, not single_day, not prayer-rows.
    match = GenericTableDetector().detect(GENERIC_HTML, "https://m.example/")
    g = match.config.grid
    assert g.transpose is False
    assert g.single_day is None
    assert g.prayer_label_index is None
    assert g.date.index == 0


def test_no_match_on_non_timetable_table():
    assert GenericTableDetector().detect(PLAIN_HTML, "https://m.example/") is None


def test_too_few_prayer_columns_returns_none():
    assert GenericTableDetector().detect(PARTIAL_HTML, "https://m.example/") is None
