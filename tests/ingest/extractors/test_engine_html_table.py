from datetime import date

from directory.domain import Prayer
from directory.ingest.extractors.config_schema import SourceConfig
from directory.ingest.extractors.engine import extract_html_table

HTML = """
<table class="times">
  <tr><th>Date</th><th>Fajr Begins</th><th>Fajr Iqamah</th><th>Dhuhr Jamaah</th></tr>
  <tr><td>1 June</td><td>2:50</td><td>3:15</td><td>1:30</td></tr>
  <tr><td>2 June</td><td>2:51</td><td>3:15</td><td>1:30</td></tr>
</table>
"""

CONFIG = SourceConfig.from_json(
    """
    {
      "shape": "html_table",
      "grid": {
        "table_selector": "table.times",
        "date": {"index": 0},
        "columns": [
          {"kind": "begin", "prayer": "fajr", "index": 1, "header_seen": "Fajr Begins"},
          {"kind": "jamaah", "prayer": "fajr", "index": 2, "header_seen": "Fajr Iqamah"},
          {"kind": "jamaah", "prayer": "dhuhr", "index": 3, "header_seen": "Dhuhr Jamaah"}
        ]
      }
    }
    """
)


def test_extracts_cells_with_resolved_dates_and_times():
    result = extract_html_table(HTML, CONFIG, year=2026)
    by = {(c.date, c.prayer, c.kind): c.time for c in result.cells}
    assert by[(date(2026, 6, 1), Prayer.FAJR, "begin")] == "02:50"
    assert by[(date(2026, 6, 1), Prayer.FAJR, "jamaah")] == "03:15"
    # dhuhr jamaah "1:30" with no am/pm marker infers pm
    assert by[(date(2026, 6, 1), Prayer.DHUHR, "jamaah")] == "13:30"
    # header row (non-date first cell) is skipped, two data rows survive
    assert len({c.date for c in result.cells}) == 2


def test_missing_table_yields_warning_not_crash():
    result = extract_html_table("<p>no table</p>", CONFIG, year=2026)
    assert result.cells == []
    assert result.warnings


# A body row with a colspan: Dhuhr+Asr share one merged 13:15 cell. Naive
# cell-flattening would shift Maghrib out of index 4; the grid model keeps it.
COLSPAN_BODY = """
<table class="t">
  <tr><th>Date</th><th>Fajr</th><th>Dhuhr</th><th>Asr</th><th>Maghrib</th></tr>
  <tr><td>3 June</td><td>05:00</td><td colspan="2">13:15</td><td>21:10</td></tr>
</table>
"""

COLSPAN_CFG = SourceConfig.from_json(
    """
    {
      "shape": "html_table",
      "grid": {
        "table_selector": "table.t",
        "date": {"index": 0},
        "columns": [
          {"kind": "jamaah", "prayer": "fajr", "index": 1},
          {"kind": "jamaah", "prayer": "dhuhr", "index": 2},
          {"kind": "jamaah", "prayer": "asr", "index": 3},
          {"kind": "jamaah", "prayer": "maghrib", "index": 4}
        ]
      }
    }
    """
)


def test_body_colspan_keeps_column_indices_aligned():
    result = extract_html_table(COLSPAN_BODY, COLSPAN_CFG, year=2026, month=6)
    by = {c.prayer: c.time for c in result.cells if c.date == date(2026, 6, 3)}
    assert by[Prayer.MAGHRIB] == "21:10"  # colspan did not shift Maghrib out of index 4
    assert by[Prayer.ASR] == "13:15"  # Asr inherits the spanned 13:15 cell


OFFSET_HTML = """
<table class="t">
  <tr><th>Date</th><th>Fajr Begins</th><th>Fajr Jamaah</th></tr>
  <tr><td>1 June</td><td>02:50</td><td>+5</td></tr>
</table>
"""

OFFSET_CFG = SourceConfig.from_json(
    """
    {
      "shape": "html_table",
      "grid": {
        "table_selector": "table.t",
        "date": {"index": 0},
        "columns": [
          {"kind": "begin", "prayer": "fajr", "index": 1},
          {"kind": "jamaah", "prayer": "fajr", "index": 2, "value_kind": "offset"}
        ]
      }
    }
    """
)


SINGLE_DAY_HTML = """
<table class="today">
  <tr><th>Fajr</th><th>Dhuhr</th><th>Asr</th><th>Maghrib</th><th>Isha</th></tr>
  <tr><td>5:00 am</td><td>1:30 pm</td><td>6:30 pm</td><td>9:30 pm</td><td>11:00 pm</td></tr>
</table>
"""

SINGLE_DAY_CFG = SourceConfig.from_json(
    """
    {
      "shape": "html_table",
      "grid": {
        "table_selector": "table.today",
        "single_day": true,
        "columns": [
          {"kind": "jamaah", "prayer": "fajr", "index": 0},
          {"kind": "jamaah", "prayer": "dhuhr", "index": 1},
          {"kind": "jamaah", "prayer": "asr", "index": 2},
          {"kind": "jamaah", "prayer": "maghrib", "index": 3},
          {"kind": "jamaah", "prayer": "isha", "index": 4}
        ]
      }
    }
    """
)


def test_single_day_table_stamps_every_cell_with_the_run_date():
    today = date(2026, 6, 22)
    result = extract_html_table(
        SINGLE_DAY_HTML, SINGLE_DAY_CFG, year=today.year, month=today.month, today=today
    )
    by = {c.prayer: c.time for c in result.cells}
    assert all(c.date == today for c in result.cells)
    assert by[Prayer.FAJR] == "05:00"
    assert by[Prayer.ISHA] == "23:00"
    assert len(result.cells) == 5  # one data row consumed, header row skipped


VERTICAL_HTML = """
<table class="vt">
  <tr><th>Waqt</th><th>Begin</th><th>Iqamah</th></tr>
  <tr><td>Fajr</td><td>2:50 am</td><td>3:45 am</td></tr>
  <tr><td>Sunrise</td><td>4:45 am</td><td>Sunrise</td></tr>
  <tr><td>Zuhr</td><td>1:09 pm</td><td>1:30 pm</td></tr>
  <tr><td>Isha</td><td>10:39 pm</td><td>11:00 pm</td></tr>
</table>
"""

VERTICAL_CFG = SourceConfig.from_json(
    """
    {
      "shape": "html_table",
      "grid": {
        "table_selector": "table.vt",
        "prayer_label_index": 0,
        "single_day": true,
        "columns": [
          {"kind": "begin", "index": 1, "header_seen": "Begin"},
          {"kind": "jamaah", "index": 2, "header_seen": "Iqamah"}
        ]
      }
    }
    """
)


def test_vertical_table_reads_prayer_from_row_label_dated_today():
    today = date(2026, 6, 22)
    result = extract_html_table(
        VERTICAL_HTML, VERTICAL_CFG, year=today.year, month=today.month, today=today
    )
    by = {(c.prayer, c.kind): c.time for c in result.cells}
    assert all(c.date == today for c in result.cells)
    assert by[(Prayer.FAJR, "begin")] == "02:50"
    assert by[(Prayer.FAJR, "jamaah")] == "03:45"
    assert by[(Prayer.DHUHR, "jamaah")] == "13:30"  # "Zuhr" label + bare pm time
    assert by[(Prayer.ISHA, "jamaah")] == "23:00"
    # the Sunrise row (not a daily prayer) and the header row are both skipped
    assert all(c.prayer in {Prayer.FAJR, Prayer.DHUHR, Prayer.ISHA} for c in result.cells)


def test_offset_column_yields_offset_cell_not_time():
    result = extract_html_table(OFFSET_HTML, OFFSET_CFG, year=2026, month=6)
    cells = {(c.prayer, c.kind): c for c in result.cells if c.date == date(2026, 6, 1)}
    begin = cells[(Prayer.FAJR, "begin")]
    jamaah = cells[(Prayer.FAJR, "jamaah")]
    assert begin.time == "02:50" and begin.offset_min is None
    assert jamaah.time is None and jamaah.offset_min == 5
    assert jamaah.base_prayer == Prayer.FAJR  # defaults to the column's own prayer
