from datetime import date

from directory.domain import Prayer
from directory.ingest.extractors.engine import extract
from directory.ingest.extractors.platforms.generic_table import GenericTableDetector
from directory.ingest.gates import run_gates
from directory.ingest.materialize import materialize

# Prayers run down the rows; columns are Waqt | Begin | Iqamah; no date axis.
# A Sunrise row (not a daily prayer) sits in the middle, and its Iqamah cell is
# the word "Sunrise" rather than a time — both must be ignored.
VERTICAL_HTML = """
<html><body>
<table class="w-full">
  <thead><tr><th>Waqt</th><th>Begin</th><th>Iqamah</th></tr></thead>
  <tbody>
    <tr><td>Fajr</td><td>2:50 am</td><td>3:45 am</td></tr>
    <tr><td>Sunrise</td><td>4:45 am</td><td>Sunrise</td></tr>
    <tr><td>Zuhr</td><td>1:09 pm</td><td>1:30 pm</td></tr>
    <tr><td>Asr</td><td>6:38 pm</td><td>7:15 pm</td></tr>
    <tr><td>Maghrib</td><td>9:20 pm</td><td>9:25 pm</td></tr>
    <tr><td>Isha</td><td>10:39 pm</td><td>11:00 pm</td></tr>
  </tbody>
</table>
</body></html>
"""


def test_detects_vertical_prayer_rows_table():
    match = GenericTableDetector().detect(VERTICAL_HTML, "https://m.example/")
    assert match is not None
    assert match.platform == "generic_table"
    g = match.config.grid
    assert g.prayer_label_index == 0
    assert g.single_day is True
    assert g.date is None
    # one begin column and one jamaah column, neither carrying a prayer
    assert {c.kind for c in g.columns} == {"begin", "jamaah"}
    assert all(c.prayer is None for c in g.columns)


def test_vertical_config_round_trips_to_auto_accept():
    today = date(2026, 6, 22)
    match = GenericTableDetector().detect(VERTICAL_HTML, "https://m.example/")
    result = extract(VERTICAL_HTML, match.config, year=today.year, month=today.month, today=today)
    rows = materialize(result, match.config, horizon_start=today, horizon_end=today)
    gate = run_gates(match.config, result, rows, html_text=VERTICAL_HTML)
    assert gate.lane == "auto_accept"
    # five daily prayers (Sunrise dropped), all dated today, each with a begin time
    assert {r.prayer for r in rows} == {"fajr", "dhuhr", "asr", "maghrib", "isha"}
    assert all(r.date == today.isoformat() for r in rows)
    assert all(r.begin_time for r in rows)
