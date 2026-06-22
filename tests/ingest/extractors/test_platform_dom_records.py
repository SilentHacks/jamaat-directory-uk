from datetime import date
from pathlib import Path

from directory.ingest.extractors.config_schema import SourceConfig
from directory.ingest.extractors.platforms.dom_records import DomRecordsDetector

FIXTURES = Path(__file__).parent.parent.parent / "fixtures"
BLACKHALL = (FIXTURES / "dom_records_blackhall.html").read_text()
URL = "https://asmuk.org/blackhall-mosque/monthly-timetable"


def test_detects_record_stream_and_requires_js():
    match = DomRecordsDetector().detect(BLACKHALL, URL, today=date(2026, 6, 1))
    assert match is not None
    assert match.platform == "dom_records"
    assert match.requires_js is True
    assert match.config.shape == "dom_records"


def test_emits_month_nav_paging():
    match = DomRecordsDetector().detect(BLACKHALL, URL, today=date(2026, 6, 1))
    paging = match.config.paging
    assert paging is not None
    assert paging.mode == "render_nav"
    assert paging.nav.kind == "next"
    assert paging.nav.next_selector  # a forward control was found


def test_config_round_trips():
    match = DomRecordsDetector().detect(BLACKHALL, URL, today=date(2026, 6, 1))
    assert SourceConfig.from_json(match.config.to_json()) == match.config


def test_no_match_on_plain_page():
    html = "<html><body><p>Welcome to our mosque. Donate today.</p></body></html>"
    assert DomRecordsDetector().detect(html, URL, today=date(2026, 6, 1)) is None


def test_single_day_card_has_no_paging():
    html = """
    <div class="card">
      <div>Fajr</div><div>05:00</div><div>Dhuhr</div><div>13:30</div>
      <div>Asr</div><div>18:30</div><div>Maghrib</div><div>21:00</div>
      <div>Isha</div><div>22:30</div>
    </div>
    """
    match = DomRecordsDetector().detect(html, URL, today=date(2026, 6, 1))
    assert match is not None
    assert match.config.paging is None
