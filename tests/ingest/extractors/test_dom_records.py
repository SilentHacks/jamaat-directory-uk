from datetime import date
from pathlib import Path

from directory.domain import Prayer
from directory.ingest.extractors.config_schema import DateSpec, GridSpec, SourceConfig
from directory.ingest.extractors.dom_records import assign_times, extract_dom_records

FIXTURES = Path(__file__).parent.parent.parent / "fixtures"
BLACKHALL = (FIXTURES / "dom_records_blackhall.html").read_text()

CONFIG = SourceConfig(shape="dom_records", grid=GridSpec(date=DateSpec(format="d_month")))
TODAY = date(2026, 6, 1)


def _cells_by_day(html):
    res = extract_dom_records(html, CONFIG, year=2026, month=6, today=TODAY)
    out: dict[date, dict[tuple, str]] = {}
    for c in res.cells:
        out.setdefault(c.date, {})[(c.prayer, c.kind)] = c.time
    return out


def test_extracts_three_days_of_five_daily_prayers():
    days = _cells_by_day(BLACKHALL)
    assert set(days) == {date(2026, 6, 1), date(2026, 6, 2), date(2026, 6, 3)}
    for d in days:
        present = {p for (p, _kind) in days[d]}
        assert present == {Prayer.FAJR, Prayer.DHUHR, Prayer.ASR, Prayer.MAGHRIB, Prayer.ISHA}


def test_later_time_is_jamaah_earlier_is_begin():
    d1 = _cells_by_day(BLACKHALL)[date(2026, 6, 1)]
    assert d1[(Prayer.FAJR, "jamaah")] == "01:45"
    assert d1[(Prayer.FAJR, "begin")] == "01:33"
    assert d1[(Prayer.DHUHR, "jamaah")] == "14:00"
    assert d1[(Prayer.DHUHR, "begin")] == "13:16"
    assert d1[(Prayer.ASR, "jamaah")] == "19:45"


def test_single_time_prayer_has_no_begin():
    d1 = _cells_by_day(BLACKHALL)[date(2026, 6, 1)]
    assert d1[(Prayer.MAGHRIB, "jamaah")] == "21:52"
    assert (Prayer.MAGHRIB, "begin") not in d1


def test_shuruq_orphan_time_not_attached_to_fajr():
    # Shuruq (04:35) is a non-prayer label; its time must not become a Fajr time.
    d1 = _cells_by_day(BLACKHALL)[date(2026, 6, 1)]
    assert "04:35" not in d1.values()


def test_hero_chrome_times_excluded():
    # The page header carries a "next prayer" widget (Dhuhr 13:00); the locator
    # must bind to the timetable, never the chrome.
    d1 = _cells_by_day(BLACKHALL)[date(2026, 6, 1)]
    assert d1[(Prayer.DHUHR, "jamaah")] == "14:00"


def test_assign_times_rules():
    assert assign_times(Prayer.DHUHR, ["14:00", "13:16"]) == ("14:00", "13:16")
    assert assign_times(Prayer.DHUHR, ["13:16", "14:00"]) == ("14:00", "13:16")
    assert assign_times(Prayer.MAGHRIB, ["21:52"]) == ("21:52", None)
    assert assign_times(Prayer.DHUHR, ["14:00", "14:00"]) == ("14:00", None)
    assert assign_times(Prayer.FAJR, []) == (None, None)


def test_single_day_card_without_dates_uses_run_day():
    html = """
    <div class="card">
      <div>Fajr</div><div>05:00</div>
      <div>Dhuhr</div><div>13:30</div>
      <div>Asr</div><div>18:30</div>
      <div>Maghrib</div><div>21:00</div>
      <div>Isha</div><div>22:30</div>
    </div>
    """
    days = _cells_by_day(html)
    assert set(days) == {TODAY}
    assert days[TODAY][(Prayer.FAJR, "jamaah")] == "05:00"
