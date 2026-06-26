"""Verified MyLocalMasjid (my-masjid.com) widget extractor + detector (C7).

The fixture is a real (trimmed to two months) GetMasjidTimings API response, so the
extractor is checked against the actual feed shape, not a synthetic guess."""

from datetime import date
from pathlib import Path

from directory.domain import Prayer
from directory.ingest.extractors.engine import extract
from directory.ingest.extractors.platforms.my_masjid import (
    MyMasjidDetector,
    extract_my_masjid,
    find_guid,
    my_masjid_data_url,
)

FIXTURE = (
    Path(__file__).parent.parent.parent / "fixtures" / "widgets" / "my_masjid_timings.json"
).read_text()

GUID = "f4c8cc40-8e42-47ce-9e74-d8125a10b0ba"
API = f"https://time.my-masjid.com/api/TimingsInfoScreen/GetMasjidTimings?GuidId={GUID}"


def test_find_guid_from_screen_url():
    assert find_guid(f"https://time.my-masjid.com/timingscreen/{GUID}") == GUID
    assert find_guid(f"//time.my-masjid.com/embed/{GUID}") == GUID
    assert find_guid("https://example.com/about") is None


def test_data_url_builds_the_api():
    assert my_masjid_data_url(f"https://time.my-masjid.com/timingscreen/{GUID}") == API
    assert my_masjid_data_url("no widget here") is None


def test_extracts_begin_and_jamaah_for_a_day():
    result = extract_my_masjid(FIXTURE, year=2026, month=6)
    # 1 June 2026 is in the fixture (month 6 retained).
    d = date(2026, 6, 1)
    cells = {(c.prayer, c.kind): c.time for c in result.cells if c.date == d}
    assert (Prayer.FAJR, "begin") in cells
    assert (Prayer.FAJR, "jamaah") in cells
    assert (Prayer.ISHA, "jamaah") in cells
    # All five daily prayers carry both a begin and a jamaah time.
    for prayer in (Prayer.FAJR, Prayer.DHUHR, Prayer.ASR, Prayer.MAGHRIB, Prayer.ISHA):
        assert (prayer, "begin") in cells
        assert (prayer, "jamaah") in cells


def test_emits_jumuah_on_fridays():
    result = extract_my_masjid(FIXTURE, year=2026, month=6)
    jumuah = [c for c in result.cells if c.prayer == Prayer.JUMUAH]
    assert jumuah, "expected Jumu'ah cells on Fridays"
    assert all(c.date.weekday() == 4 for c in jumuah)


def test_non_json_payload_yields_no_cells_without_raising():
    result = extract_my_masjid("<html>not json</html>", year=2026, month=6)
    assert result.cells == []
    assert result.warnings


def test_detector_authors_widget_from_anchor_button():
    # The mosque's own page links to the screen with a button (no iframe).
    html = (
        f'<html><body><a href="https://time.my-masjid.com/timingscreen/{GUID}">'
        f'Prayer Times</a></body></html>'
    )
    match = MyMasjidDetector().detect(html, "https://mosque.example/")
    assert match is not None
    assert match.platform == "mylocalmasjid"
    assert match.url == API
    assert match.config.shape == "widget"
    assert match.config.widget.platform == "mylocalmasjid"
    assert match.config.widget.data_url == API
    assert match.requires_js is False


def test_detector_authors_widget_from_screen_url_itself():
    match = MyMasjidDetector().detect("", f"https://time.my-masjid.com/timingscreen/{GUID}")
    assert match is not None
    assert match.url == API


def test_detector_ignores_unrelated_pages():
    assert MyMasjidDetector().detect("<html>nope</html>", "https://x.example/") is None


def test_widget_config_extracts_via_engine_with_api_fetch():
    """End-to-end: the engine's widget path reads the fetched API JSON through the
    registered extractor."""
    from directory.ingest.extractors.config_schema import SourceConfig, WidgetSpec

    config = SourceConfig(shape="widget", widget=WidgetSpec(platform="mylocalmasjid", data_url=API))
    result = extract(FIXTURE, config, year=2026, month=6)
    assert any(c.prayer == Prayer.FAJR for c in result.cells)
