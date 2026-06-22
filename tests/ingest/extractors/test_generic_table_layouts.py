from datetime import date

from directory.ingest.extractors.engine import extract
from directory.ingest.extractors.platforms.generic_table import GenericTableDetector
from directory.ingest.gates import run_gates
from directory.ingest.materialize import materialize

# --- Horizontal single-day: prayers in the header, one "today" row, no date ---
HORIZONTAL_SINGLE_HTML = """
<html><body>
<table class="today">
  <tr><th>Fajr</th><th>Dhuhr</th><th>Asr</th><th>Maghrib</th><th>Isha</th></tr>
  <tr><td>5:00 am</td><td>1:30 pm</td><td>6:30 pm</td><td>9:30 pm</td><td>11:00 pm</td></tr>
</table>
</body></html>
"""


def test_detects_horizontal_single_day_table():
    match = GenericTableDetector().detect(HORIZONTAL_SINGLE_HTML, "https://m.example/")
    assert match is not None
    g = match.config.grid
    assert g.single_day is True
    assert g.prayer_label_index is None
    assert g.date is None
    assert len(g.columns) == 5


def test_horizontal_single_day_round_trips_to_auto_accept():
    today = date(2026, 6, 22)
    match = GenericTableDetector().detect(HORIZONTAL_SINGLE_HTML, "https://m.example/")
    result = extract(
        HORIZONTAL_SINGLE_HTML, match.config, year=today.year, month=today.month, today=today
    )
    rows = materialize(result, match.config, horizon_start=today, horizon_end=today)
    gate = run_gates(match.config, result, rows, html_text=HORIZONTAL_SINGLE_HTML)
    assert gate.lane == "auto_accept"
    assert {r.date for r in rows} == {today.isoformat()}


# --- Transpose multi-day: prayers down the side, dates across the top ---
TRANSPOSE_HTML = """
<html><body>
<table class="grid">
  <tr><th>Prayer</th><th>1 June</th><th>2 June</th><th>3 June</th></tr>
  <tr><td>Fajr</td><td>05:00</td><td>05:01</td><td>05:02</td></tr>
  <tr><td>Dhuhr</td><td>13:30</td><td>13:30</td><td>13:30</td></tr>
  <tr><td>Asr</td><td>18:30</td><td>18:31</td><td>18:32</td></tr>
  <tr><td>Maghrib</td><td>21:30</td><td>21:31</td><td>21:32</td></tr>
  <tr><td>Isha</td><td>23:00</td><td>23:00</td><td>23:00</td></tr>
</table>
</body></html>
"""


def test_detects_transpose_multiday_table():
    match = GenericTableDetector().detect(TRANSPOSE_HTML, "https://m.example/")
    assert match is not None
    g = match.config.grid
    assert g.transpose is True
    assert g.date is not None
    assert len(g.columns) == 5


# A multi-row table whose "date" column is unparseable must NOT be mistaken for a
# single-day snapshot (which would silently drop every row but one).
MULTIROW_NODATE_HTML = """
<html><body>
<table>
  <tr><th>When</th><th>Fajr</th><th>Dhuhr</th><th>Asr</th><th>Maghrib</th><th>Isha</th></tr>
  <tr><td>foo</td><td>05:00</td><td>13:15</td><td>18:30</td><td>21:10</td><td>22:30</td></tr>
  <tr><td>bar</td><td>05:02</td><td>13:16</td><td>18:31</td><td>21:11</td><td>22:31</td></tr>
</table>
</body></html>
"""


def test_multi_row_table_without_parseable_dates_is_not_collapsed():
    assert GenericTableDetector().detect(MULTIROW_NODATE_HTML, "https://m.example/") is None


def test_transpose_config_extracts_every_day():
    match = GenericTableDetector().detect(TRANSPOSE_HTML, "https://m.example/")
    result = extract(TRANSPOSE_HTML, match.config, year=2026, month=6)
    assert len({c.date for c in result.cells}) == 3  # one column per date, all read
    rows = materialize(
        result, match.config, horizon_start=date(2026, 6, 1), horizon_end=date(2026, 6, 30)
    )
    gate = run_gates(match.config, result, rows, html_text=TRANSPOSE_HTML)
    assert gate.lane == "auto_accept"
