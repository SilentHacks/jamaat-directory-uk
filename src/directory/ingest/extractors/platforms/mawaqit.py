import json
from datetime import date

from directory.domain import DAILY_PRAYERS
from directory.ingest.extractors.config_schema import SourceConfig, WidgetSpec
from directory.ingest.extractors.engine import Cell, ExtractionResult, register_widget
from directory.ingest.extractors.platforms.base import PlatformMatch, register
from directory.ingest.jsonscan import first_json_object
from directory.ingest.normalize import parse_offset, parse_time

# Mawaqit calendar rows are [fajr, shuruq, dhuhr, asr, maghrib, isha] when six
# values are present; older/simpler feeds may omit shuruq and carry five.
_ORDER = list(DAILY_PRAYERS)
_SIX_COL = (0, 2, 3, 4, 5)


def _prayer_times(times: list) -> list:
    if len(times) >= 6:
        return [times[i] for i in _SIX_COL]
    return list(times[: len(_ORDER)])


def _parse_confdata(html: str) -> dict | None:
    obj = first_json_object(html, after="confData")
    if obj is None:
        return None
    try:
        return json.loads(obj)
    except json.JSONDecodeError:
        return None


def _month_cells(month_map: dict, kind: str, *, year: int, month: int) -> list[Cell]:
    cells: list[Cell] = []
    for day_str, times in month_map.items():
        try:
            day = int(day_str)
        except ValueError:
            continue
        for prayer, raw in zip(_ORDER, _prayer_times(list(times)), strict=False):
            t = parse_time(raw, prefer_pm=(prayer != _ORDER[0]))
            if t is not None:
                cells.append(Cell(date=date(year, month, day), prayer=prayer, kind=kind, time=t))
                continue
            off = parse_offset(raw)
            if off is None:
                continue
            cells.append(
                Cell(
                    date=date(year, month, day), prayer=prayer, kind=kind, time=None,
                    offset_min=off, base_prayer=prayer,
                )
            )
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
        result.cells.extend(_month_cells(begin_cal[idx], "begin", year=year, month=month))
    if 0 <= idx < len(jamaah_cal) and jamaah_cal[idx]:
        result.cells.extend(_month_cells(jamaah_cal[idx], "jamaah", year=year, month=month))
    return result


class MawaqitDetector:
    name = "mawaqit"

    def detect(self, html: str, url: str, *, fetcher=None) -> PlatformMatch | None:
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
