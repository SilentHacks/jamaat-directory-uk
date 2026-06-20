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
