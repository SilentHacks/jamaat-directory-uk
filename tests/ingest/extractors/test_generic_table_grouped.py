from datetime import date

from directory.domain import DAILY_PRAYERS, Prayer
from directory.ingest.extractors.engine import extract
from directory.ingest.extractors.platforms.generic_table import GenericTableDetector
from directory.ingest.gates import run_gates
from directory.ingest.materialize import materialize

# Aberdeen-style grouped header: a two-row thead where each prayer owns a
# colspan group of Begins/Jamā‘ah sub-columns (Fajr also has Sunrise), and
# Date/Day live in the lower header row. Real transliteration spellings.
GROUPED = """<html><body><table class="prayer">
<thead>
 <tr><th></th><th></th><th colspan="3">Fajr</th><th colspan="2">Zuhr</th>
     <th colspan="2">Asr</th><th colspan="2">Maghrib</th><th colspan="2">Ishā</th></tr>
 <tr><th>Date</th><th>Day</th><th>Begins</th><th>Jamā‘ah</th><th>Sunrise</th>
     <th>Begins</th><th>Jamā‘ah</th><th>Begins</th><th>Jamā‘ah</th>
     <th>Begins</th><th>Jamā‘ah</th><th>Begins</th><th>Jamā‘ah</th></tr>
</thead>
<tbody>
 <tr><td>1 June</td><td>Mon</td><td>02:50</td><td>03:15</td><td>04:30</td>
     <td>13:00</td><td>13:30</td><td>17:00</td><td>17:30</td>
     <td>21:30</td><td>21:35</td><td>23:00</td><td>23:15</td></tr>
 <tr><td>2 June</td><td>Tue</td><td>02:51</td><td>03:15</td><td>04:31</td>
     <td>13:00</td><td>13:30</td><td>17:01</td><td>17:30</td>
     <td>21:31</td><td>21:36</td><td>23:01</td><td>23:15</td></tr>
</tbody></table></body></html>"""

URL = "https://m.example/prayer-times"


def test_detects_begin_and_jamaah_column_per_prayer():
    match = GenericTableDetector().detect(GROUPED, URL)
    assert match is not None
    assert match.platform == "generic_table"
    cols = match.config.grid.columns
    pairs = {(c.prayer, c.kind) for c in cols}
    for p in DAILY_PRAYERS:
        assert (p, "begin") in pairs, f"missing begin for {p}"
        assert (p, "jamaah") in pairs, f"missing jamaah for {p}"


def test_sunrise_column_is_dropped():
    match = GenericTableDetector().detect(GROUPED, URL)
    headers = " ".join((c.header_seen or "").lower() for c in match.config.grid.columns)
    assert "sunrise" not in headers


def test_date_column_detected_in_lower_header_row():
    match = GenericTableDetector().detect(GROUPED, URL)
    assert match.config.grid.date.index == 0


# Same grouped shape, but every Jamā‘ah column holds a "+N" offset from Begins.
OFFSET_GROUPED = """<html><body><table class="prayer">
<thead>
 <tr><th></th><th></th><th colspan="2">Fajr</th><th colspan="2">Zuhr</th>
     <th colspan="2">Asr</th><th colspan="2">Maghrib</th><th colspan="2">Ishā</th></tr>
 <tr><th>Date</th><th>Day</th><th>Begins</th><th>Jamā‘ah</th>
     <th>Begins</th><th>Jamā‘ah</th><th>Begins</th><th>Jamā‘ah</th>
     <th>Begins</th><th>Jamā‘ah</th><th>Begins</th><th>Jamā‘ah</th></tr>
</thead>
<tbody>
 <tr><td>1 June</td><td>Mon</td><td>03:00</td><td>+25</td><td>13:00</td><td>+10</td>
     <td>17:00</td><td>+10</td><td>21:30</td><td>+5</td><td>22:45</td><td>+15</td></tr>
 <tr><td>2 June</td><td>Tue</td><td>03:01</td><td>+25</td><td>13:00</td><td>+10</td>
     <td>17:01</td><td>+10</td><td>21:31</td><td>+5</td><td>22:46</td><td>+15</td></tr>
</tbody></table></body></html>"""


def test_offset_jamaah_columns_author_and_resolve_end_to_end():
    match = GenericTableDetector().detect(OFFSET_GROUPED, URL)
    assert match is not None
    jamaah = [c for c in match.config.grid.columns if c.kind == "jamaah"]
    assert jamaah and all(c.value_kind == "offset" for c in jamaah)

    result = extract(OFFSET_GROUPED, match.config, year=2026, month=6)
    rows = materialize(
        result, match.config, horizon_start=date(2026, 6, 1), horizon_end=date(2026, 6, 30)
    )
    gate = run_gates(match.config, result, rows, html_text=OFFSET_GROUPED)
    assert gate.lane == "auto_accept"

    day1 = {r.prayer: r for r in rows if r.date == "2026-06-01"}
    assert day1["fajr"].jamaah_time == "03:25"  # 03:00 + 25
    assert day1["fajr"].derived is True
    assert day1["isha"].jamaah_time == "23:00"  # 22:45 + 15


def test_offset_columns_without_a_begin_column_are_not_authored():
    # +N jamaah with no Begins to resolve against is unmaterializable → dropped,
    # leaving too few columns to author.
    html = """<table>
      <tr><th>Date</th><th>Fajr</th><th>Dhuhr</th><th>Asr</th></tr>
      <tr><td>1 June</td><td>+5</td><td>+10</td><td>+10</td></tr>
    </table>"""
    assert GenericTableDetector().detect(html, URL) is None


def test_grouped_config_round_trips_to_auto_accept():
    match = GenericTableDetector().detect(GROUPED, URL)
    result = extract(GROUPED, match.config, year=2026, month=6)
    rows = materialize(
        result, match.config, horizon_start=date(2026, 6, 1), horizon_end=date(2026, 6, 30)
    )
    gate = run_gates(match.config, result, rows, html_text=GROUPED)
    assert gate.lane == "auto_accept"
    day1 = {
        c.kind: c.time
        for c in result.cells
        if c.prayer == Prayer.FAJR and c.date == date(2026, 6, 1)
    }
    assert day1 == {"begin": "02:50", "jamaah": "03:15"}
