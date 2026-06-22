"""Detector for the WordPress "Daily Prayer Time for Mosques" plugin.

The plugin (slug ``daily-prayer-time-for-mosques``, often on a Divi theme) shows
only a single-day widget inline; its full **monthly** grid is loaded over
``admin-ajax.php`` (``action=get_monthly_timetable&month=N``) and returned as a
clean ``<table class="dptTimetable">``. So this detector — given the widget page —
derives that endpoint, fetches one month to author the column map, and emits a
``url_template`` paging config. The daily extract then fetches each month of the
horizon over plain HTTP (no browser, £0) and reuses the ``html_table`` engine.

The monthly grid is daily-prayers-only (Adhan/Iqamah per prayer); Jumu‘ah is read
separately from the day widget into a fixed ``jumuah`` block.
"""

import re
from datetime import date

from bs4 import BeautifulSoup

from directory.ingest.extractors.column_author import author_grid
from directory.ingest.extractors.config_schema import (
    JumuahSessionSpec,
    JumuahSpec,
    PagingSpec,
    SourceConfig,
)
from directory.ingest.extractors.platforms.base import Fetcher, PlatformMatch
from directory.ingest.normalize import parse_time

# Plugin fingerprints on the handed page. The plugin path and its JS config var
# are highly specific; the table class alone is only corroborating.
_PLUGIN_SLUG = "daily-prayer-time-for-mosques"
_PARAMS_VAR = "timetable_params"

_AJAXURL_RE = re.compile(
    r"timetable_params\s*=\s*\{[^}]*?[\"']ajaxurl[\"']\s*:\s*[\"']([^\"']+)[\"']",
    re.DOTALL,
)
_ACTION = "get_monthly_timetable"
_JUMUAH_WINDOW = 200  # chars of visible text after "jumu" to scan for sessions
_ORDINAL_RE = re.compile(r"\b(\d(?:st|nd|rd|th)|first|second|third|fourth)\b", re.IGNORECASE)
_TIME_RE = re.compile(r"\d{1,2}[:.]\d{2}")


def _ajaxurl(html: str, url: str) -> str | None:
    """The plugin's ``admin-ajax.php`` URL from ``timetable_params`` (slashes may
    be JSON-escaped), falling back to the site's conventional WP path."""
    m = _AJAXURL_RE.search(html)
    if m:
        return m.group(1).replace("\\/", "/")
    from urllib.parse import urljoin

    return urljoin(url, "/wp-admin/admin-ajax.php")


def _build_template(ajaxurl: str) -> str | None:
    sep = "&" if "?" in ajaxurl else "?"
    template = f"{ajaxurl}{sep}action={_ACTION}&month={{month}}&display=table"
    try:  # must be a safe format string for the pager (only {month}/{year})
        template.format(year=2000, month=1)
    except (KeyError, ValueError, IndexError):
        return None
    return template


def _jumuah_label(raw: str | None, idx: int) -> str:
    ordinal = raw.strip() if raw else {1: "1st", 2: "2nd", 3: "3rd", 4: "4th"}.get(idx, f"{idx}th")
    return f"{ordinal} Jumu'ah"


def _parse_jumuah(html: str) -> JumuahSpec | None:
    """Read the day widget's Jumu‘ah sessions (e.g. "1st Iqamah 13:30 / 2nd
    Iqamah 14:30") into a fixed block. Only times in the Friday-midday window are
    taken, so neighbouring daily times can never leak in. None if no session."""
    text = BeautifulSoup(html, "lxml").get_text(" ", strip=True)
    start = text.lower().find("jumu")
    if start < 0:
        return None
    window = text[start : start + _JUMUAH_WINDOW]
    sessions: list[JumuahSessionSpec] = []
    seen: set[str] = set()
    for m in _TIME_RE.finditer(window):
        t = parse_time(m.group(0))
        if t is None:
            return None if not sessions else JumuahSpec(source="fixed", sessions=sessions)
        minutes = int(t[:2]) * 60 + int(t[3:])
        if not (12 * 60 <= minutes <= 15 * 60) or t in seen:
            continue
        seen.add(t)
        ords = _ORDINAL_RE.findall(window[: m.start()])
        label = _jumuah_label(ords[-1] if ords else None, len(sessions) + 1)
        sessions.append(JumuahSessionSpec(label=label, time=t))
    return JumuahSpec(source="fixed", sessions=sessions) if sessions else None


class WpDptDetector:
    name = "wp_dpt"

    def detect(
        self, html: str, url: str, *, fetcher: Fetcher | None = None
    ) -> PlatformMatch | None:
        low = html.lower()
        if _PLUGIN_SLUG not in low and _PARAMS_VAR not in low:
            return None
        if fetcher is None:  # the month grid is only reachable via a fetch
            return None
        ajaxurl = _ajaxurl(html, url)
        if not ajaxurl:
            return None
        template = _build_template(ajaxurl)
        if template is None:
            return None

        # Fetch one month to author the column map the widget page lacks.
        run_day = date.today()
        res = fetcher(template.format(year=run_day.year, month=run_day.month))
        if getattr(res, "error", None) or not getattr(res, "html", None):
            return None
        if getattr(res, "status", 200) >= 400:
            return None
        table = BeautifulSoup(res.html, "lxml").select_one("table.dptTimetable")
        if table is None:
            return None
        grid = author_grid(table, "table.dptTimetable")
        if grid is None:
            return None

        config = SourceConfig(
            shape="html_table",
            grid=grid,
            jumuah=_parse_jumuah(html),
            paging=PagingSpec(mode="url_template", url_template=template),
        )
        return PlatformMatch(platform=self.name, url=url, requires_js=False, config=config)
