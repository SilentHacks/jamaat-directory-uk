from directory.domain import Prayer
from directory.ingest.extractors.engine import extract
from directory.ingest.extractors.platforms.wp_prayer import WpPrayerDetector

WP_HTML = """
<html><head><meta name="generator" content="WordPress 6.5"></head><body>
<table class="dpt_table">
  <tr><th>Date</th><th>Fajr</th><th>Dhuhr</th><th>Asr</th><th>Maghrib</th><th>Isha</th></tr>
  <tr><td>1</td><td>05:00</td><td>13:15</td><td>18:30</td><td>21:10</td><td>22:30</td></tr>
  <tr><td>2</td><td>05:01</td><td>13:15</td><td>18:31</td><td>21:11</td><td>22:31</td></tr>
</table></body></html>
"""


def test_detects_and_builds_usable_config():
    match = WpPrayerDetector().detect(WP_HTML, "https://m.example/prayer-times")
    assert match is not None
    assert match.platform == "wp_prayer"
    assert match.requires_js is False
    cfg = match.config
    assert cfg.shape == "html_table"
    assert cfg.grid.date.index == 0
    prayers = {c.prayer for c in cfg.grid.columns}
    assert {Prayer.FAJR, Prayer.DHUHR, Prayer.ASR, Prayer.MAGHRIB, Prayer.ISHA} <= prayers


def test_emitted_config_extracts_cells_with_phase2_engine():
    match = WpPrayerDetector().detect(WP_HTML, "https://m.example/prayer-times")
    result = extract(WP_HTML, match.config, year=2026, month=6)
    fajr = [c for c in result.cells if c.prayer == Prayer.FAJR]
    assert {c.time for c in fajr} == {"05:00", "05:01"}


def test_no_match_on_plain_page():
    plain = "<html><body>hello</body></html>"
    assert WpPrayerDetector().detect(plain, "https://m.example/") is None
