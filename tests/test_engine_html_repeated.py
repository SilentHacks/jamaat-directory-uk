from datetime import date

from directory.domain import Prayer
from directory.ingest.extractors.config_schema import SourceConfig
from directory.ingest.extractors.engine import extract, extract_html_repeated

HTML = """
<div class="day"><span class="d">21 June</span>
  <span class="fajr">04:30</span><span class="dhuhr">13:30</span></div>
<div class="day"><span class="d">22 June</span>
  <span class="fajr">04:31</span><span class="dhuhr">13:30</span></div>
"""

CONFIG = SourceConfig.from_json(
    """
    {
      "shape": "html_repeated",
      "grid": {
        "row_selector": "div.day",
        "date": {"selector": "span.d"},
        "columns": [
          {"kind": "jamaah", "prayer": "fajr", "selector": "span.fajr"},
          {"kind": "jamaah", "prayer": "dhuhr", "selector": "span.dhuhr"}
        ]
      }
    }
    """
)


def test_repeated_items_extracted():
    result = extract_html_repeated(HTML, CONFIG, year=2026)
    by = {(c.date, c.prayer): c.time for c in result.cells}
    assert by[(date(2026, 6, 21), Prayer.FAJR)] == "04:30"
    assert by[(date(2026, 6, 22), Prayer.DHUHR)] == "13:30"


def test_dispatch_routes_by_shape():
    assert extract(HTML, CONFIG, year=2026).cells  # html_repeated via extract()
    rules_cfg = SourceConfig.from_json(
        '{"shape":"rules","rules":{"rules":[{"prayer":"dhuhr","fixed":"13:30"}]}}'
    )
    assert extract("", rules_cfg, year=2026).cells == []
