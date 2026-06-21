from directory.domain import Prayer
from directory.ingest.extractors.engine import extract
from directory.ingest.extractors.platforms.mawaqit import MawaqitDetector

# Minimal faithful confData: month index 5 == June; first day has all five prayers.
MAWAQIT_HTML = """
<html><body>
<script>
var confData = {
  "name": "Test Masjid",
  "calendar": [ {}, {}, {}, {}, {},
    { "1": ["05:00","13:00","18:00","21:00","22:00"],
      "2": ["05:01","13:00","18:01","21:01","22:01"] } ],
  "iqamaCalendar": [ {}, {}, {}, {}, {},
    { "1": ["05:15","13:15","18:15","21:10","22:15"],
      "2": ["05:16","13:15","18:16","21:11","22:16"] } ]
};
</script>
<a href="https://mawaqit.net/en/test-masjid">mawaqit</a>
</body></html>
"""


def test_detects_mawaqit_embed():
    match = MawaqitDetector().detect(MAWAQIT_HTML, "https://m.example/")
    assert match is not None
    assert match.platform == "mawaqit"
    assert match.config.shape == "widget"
    assert match.config.widget.platform == "mawaqit"


def test_extractor_emits_begin_and_jamaah_cells():
    match = MawaqitDetector().detect(MAWAQIT_HTML, "https://m.example/")
    result = extract(MAWAQIT_HTML, match.config, year=2026, month=6)
    fajr_jamaah = [c for c in result.cells if c.prayer == Prayer.FAJR and c.kind == "jamaah"]
    fajr_begin = [c for c in result.cells if c.prayer == Prayer.FAJR and c.kind == "begin"]
    assert {c.time for c in fajr_jamaah} == {"05:15", "05:16"}
    assert {c.time for c in fajr_begin} == {"05:00", "05:01"}


def test_no_match_without_mawaqit():
    assert MawaqitDetector().detect("<html></html>", "https://m.example/") is None
