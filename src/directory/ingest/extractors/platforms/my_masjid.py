"""MyLocalMasjid / my-masjid.com prayer-time screens.

A my-masjid "timing screen" (``time.my-masjid.com/timingscreen/<guid>``) is an
Angular shell whose timetable is **not** in the page — it loads a perpetual
day+month calendar from a JSON API keyed by the screen GUID:

    https://time.my-masjid.com/api/TimingsInfoScreen/GetMasjidTimings?GuidId=<guid>

So we author a ``widget`` config whose ``data_url`` is that API; the daily run
fetches the JSON and this extractor parses it (begin + iqamah for the five daily
prayers, plus Jumu'ah on Fridays). Mirrors the Mawaqit pattern, but the data lives
off the page, so the GUID is recovered from whatever references the screen — an
``<iframe>``, an ``<a href>`` button on the mosque's own site, or the screen URL
itself — which is why ``data_url`` is resolved up-front in evidence/detection rather
than read from the handed HTML.
"""

import json
import re
from datetime import date, timedelta

from directory.domain import Prayer
from directory.ingest.extractors.config_schema import SourceConfig, WidgetSpec
from directory.ingest.extractors.engine import Cell, ExtractionResult, register_widget
from directory.ingest.extractors.platforms.base import PlatformMatch, register
from directory.ingest.normalize import parse_time

_API = "https://time.my-masjid.com/api/TimingsInfoScreen/GetMasjidTimings?GuidId={guid}"

# A my-masjid screen reference: the GUID sits after the screen path segment, in an
# iframe src, an anchor href, or the bare URL. Host may be time.my-masjid.com or a
# protocol-relative //my-masjid.com/...; accept any path id (verification confirms).
_GUID_RE = re.compile(
    r"my-masjid\.com/(?:timingscreen|embed|timing|screen|infoscreen|widget)/([A-Za-z0-9._-]+)",
    re.IGNORECASE,
)

# salahTimings field names for each daily prayer: (begin, iqamah/jamaah).
_PRAYER_FIELDS: dict[Prayer, tuple[str, str]] = {
    Prayer.FAJR: ("fajr", "iqamah_Fajr"),
    Prayer.DHUHR: ("zuhr", "iqamah_Zuhr"),
    Prayer.ASR: ("asr", "iqamah_Asr"),
    Prayer.MAGHRIB: ("maghrib", "iqamah_Maghrib"),
    Prayer.ISHA: ("isha", "iqamah_Isha"),
}


def find_guid(text: str) -> str | None:
    """The my-masjid screen GUID referenced anywhere in ``text`` (a URL or HTML)."""
    m = _GUID_RE.search(text or "")
    return m.group(1) if m else None


def my_masjid_data_url(text: str) -> str | None:
    """The JSON timings API URL for a my-masjid screen referenced in ``text``, or
    None when no my-masjid screen is referenced. Used by evidence/detection to point
    a widget config at the data the page itself does not contain."""
    guid = find_guid(text)
    return _API.format(guid=guid) if guid else None


def _cell(d: date, prayer: Prayer, kind: str, raw: str | None) -> Cell | None:
    if not raw:
        return None
    # The feed is 24-hour, but prefer_pm keeps a stray 12-hour evening value honest
    # (fajr/shouruq are the only morning prayers here).
    t = parse_time(raw, prefer_pm=(prayer not in (Prayer.FAJR,)))
    if t is None:
        return None
    return Cell(date=d, prayer=prayer, kind=kind, time=t)


def _jumuah_cells(model: dict, *, year: int, months: set[int]) -> list[Cell]:
    """Emit a JUMU'AH cell on every Friday of the requested months, using the
    primary configured Jumu'ah time/iqamah. my-masjid stores one weekly time, not a
    per-day value, so it is stamped onto each Friday in range."""
    sessions = model.get("jumahSalahIqamahTimings") or []
    primary = next((s for s in sessions if s.get("isPrimary")), sessions[0] if sessions else None)
    if not primary:
        return []
    cells: list[Cell] = []
    for month in sorted(months):
        d = date(year, month, 1)
        while d.month == month:
            if d.weekday() == 4:  # Friday
                begin = _cell(d, Prayer.JUMUAH, "begin", primary.get("time"))
                jamaah = _cell(d, Prayer.JUMUAH, "jamaah", primary.get("iqamahTime"))
                cells.extend(c for c in (begin, jamaah) if c is not None)
            d += timedelta(days=1)
    return cells


def extract_my_masjid(payload: str, *, year: int, month: int | None) -> ExtractionResult:
    """Parse the GetMasjidTimings JSON into begin/jamaah cells for the whole year in
    the feed (a perpetual day+month calendar), stamped with ``year`` so a horizon
    that crosses a month boundary fills from a single fetch. ``month`` is ignored:
    the feed carries every month. Non-JSON input (e.g. a handed HTML page) yields no
    cells rather than raising, so a misrouted call simply fails verification."""
    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        return ExtractionResult(warnings=["my-masjid payload is not JSON"])
    model = data.get("model") if isinstance(data, dict) else None
    rows = (model or {}).get("salahTimings") if isinstance(model, dict) else None
    if not rows:
        return ExtractionResult(warnings=["my-masjid payload has no salahTimings"])

    result = ExtractionResult()
    months_seen: set[int] = set()
    for row in rows:
        try:
            d = date(year, int(row["month"]), int(row["day"]))
        except (KeyError, ValueError, TypeError):
            continue
        months_seen.add(d.month)
        for prayer, (begin_field, iqamah_field) in _PRAYER_FIELDS.items():
            for kind, field in (("begin", begin_field), ("jamaah", iqamah_field)):
                cell = _cell(d, prayer, kind, row.get(field))
                if cell is not None:
                    result.cells.append(cell)
    result.cells.extend(_jumuah_cells(model, year=year, months=months_seen))
    return result


class MyMasjidDetector:
    """Detect a my-masjid screen referenced by an iframe, an anchor button, or the
    handed URL, and author the JSON-API ``widget`` config. Verification fetches the
    API ``data_url`` (not the handed page), so this resolves end-to-end through the
    enumerator's ``detect_candidates`` path."""

    name = "mylocalmasjid"

    def detect(self, html: str, url: str, *, fetcher=None) -> PlatformMatch | None:
        data_url = my_masjid_data_url(html) or my_masjid_data_url(url)
        if data_url is None:
            return None
        config = SourceConfig(
            shape="widget", widget=WidgetSpec(platform=self.name, data_url=data_url)
        )
        return PlatformMatch(
            platform=self.name, url=data_url, requires_js=False, config=config
        )


register(MyMasjidDetector())
register_widget("mylocalmasjid", extract_my_masjid)
