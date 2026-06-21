import json
from datetime import date

from directory.domain import DAILY_PRAYERS
from directory.ingest.extractors.config_schema import SourceConfig, WidgetSpec
from directory.ingest.extractors.engine import Cell, ExtractionResult, register_widget
from directory.ingest.extractors.platforms.base import PlatformMatch, register
from directory.ingest.jsonscan import first_json_object
from directory.ingest.normalize import parse_time

# Mawaqit calendar rows are ordered [fajr, dhuhr, asr, maghrib, isha].
_ORDER = list(DAILY_PRAYERS)


def _parse_confdata(html: str) -> dict | None:
    obj = first_json_object(html, after="confData")
    if obj is None:
        return None
    try:
        return json.loads(obj)
    except json.JSONDecodeError:
        return None


def _month_cells(month_map: dict, prayer_order, kind: str, *, year: int, month: int) -> list[Cell]:
    cells: list[Cell] = []
    for day_str, times in month_map.items():
        try:
            day = int(day_str)
        except ValueError:
            continue
        for prayer, raw in zip(prayer_order, times, strict=False):
            t = parse_time(raw, prefer_pm=(prayer != prayer_order[0]))
            if t is None:
                continue
            cells.append(Cell(date=date(year, month, day), prayer=prayer, kind=kind, time=t))
    return cells


def extract_mawaqit(payload: str, *, year: int, month: int | None) -> ExtractionResult:
    conf = _parse_confdata(payload)
    if conf is None or month is None:
        return ExtractionResult(warnings=["confData not found"])
    idx = month - 1
    result = ExtractionResult()
    begin_cal = conf.get("calendar") or []
    jamaah_cal = conf.get("iqamaCalendar") or []
    if 0 <= idx < len(begin_cal) and begin_cal[idx]:
        result.cells.extend(_month_cells(begin_cal[idx], _ORDER, "begin", year=year, month=month))
    if 0 <= idx < len(jamaah_cal) and jamaah_cal[idx]:
        result.cells.extend(_month_cells(jamaah_cal[idx], _ORDER, "jamaah", year=year, month=month))
    return result


class MawaqitDetector:
    name = "mawaqit"

    def detect(self, html: str, url: str) -> PlatformMatch | None:
        if "mawaqit.net" not in html and "confData" not in html:
            return None
        if _parse_confdata(html) is None:
            return None
        config = SourceConfig(
            shape="widget", widget=WidgetSpec(platform="mawaqit", data_url=url)
        )
        return PlatformMatch(platform=self.name, url=url, requires_js=False, config=config)


register(MawaqitDetector())
register_widget("mawaqit", extract_mawaqit)
