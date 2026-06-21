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


def test_offset_column_yields_offset_cell_not_time():
    result = extract_html_table(OFFSET_HTML, OFFSET_CFG, year=2026, month=6)
    cells = {(c.prayer, c.kind): c for c in result.cells if c.date == date(2026, 6, 1)}
    begin = cells[(Prayer.FAJR, "begin")]
    jamaah = cells[(Prayer.FAJR, "jamaah")]
    assert begin.time == "02:50" and begin.offset_min is None
    assert jamaah.time is None and jamaah.offset_min == 5
    assert jamaah.base_prayer == Prayer.FAJR  # defaults to the column's own prayer
