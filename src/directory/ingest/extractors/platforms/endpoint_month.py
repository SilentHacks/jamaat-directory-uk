"""Generic detector for month-at-a-time timetables behind a data endpoint or a
month ``<select>`` — the generalised form of the ``wp_dpt`` mechanism for plugins
we do not recognise by name.

Two strategies, cheapest first:

* **3a — endpoint derivation (£0, no browser).** When the page wires an
  ``admin-ajax.php`` (or similar) call that takes a ``month`` parameter, derive a
  ``url_template``, fetch one month, and author it as an ``html_table``. The daily
  run then fetches each month over plain HTTP.
* **3b — ``<select>``-driven navigation (browser, still deterministic).** When the
  rendered page carries a month grid plus a month ``<select>``, author that grid
  and attach a ``render_nav`` paging spec so the daily run drives the dropdown
  across the horizon.

Either way the output is the same ``html_table`` config every other shape emits,
so a recognised month timetable is captured deterministically before the LLM tier.
"""

import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from directory.ingest.extractors.column_author import author_grid
from directory.ingest.extractors.config_schema import PagingSpec, SourceConfig
from directory.ingest.extractors.nav_detect import detect_month_nav, leaf_tags, month_select
from directory.ingest.extractors.platforms.base import Fetcher, PlatformMatch
from directory.ingest.normalize import resolve_prayer

# Endpoint-derivation guards: only attempt when the page both wires a data
# endpoint and looks prayer-related, so we never fan out fetches on arbitrary
# admin-ajax pages.
_ENDPOINT_MARKERS = ("admin-ajax.php", "/wp-json/")
_PRAYER_HINTS = ("prayer", "salah", "salat", "namaz", "timetable", "iqamah", "jamaah")
# An AJAX action paired with a month parameter; the action name is captured.
_ACTION_RE = re.compile(r"""['"]?action['"]?\s*[:=]\s*['"]([a-zA-Z0-9_]+)['"]""")
_AJAXURL_RE = re.compile(r"""['"](https?://[^'"]*?/admin-ajax\.php)['"]""")
_SCRIPT_KEYWORDS = (
    "prayer", "timetable", "salah", "namaz", "masjid", "mosque",
    "dpt", "mptt", "calendar", "month", "iqamah",
)
_ACTION_KEYWORDS = ("month", "timetable", "prayer", "calendar", "salah", "iqamah")
_MAX_SCRIPTS = 4
_DISPLAY_VARIANTS = ("&display=table", "")


def _is_prayer_related(html: str) -> bool:
    text = BeautifulSoup(html, "lxml").get_text(" ", strip=True)
    low = text.lower()
    if any(h in low for h in _PRAYER_HINTS):
        return True
    found = {
        m.prayer
        for tok in re.findall(r"[A-Za-zÀ-ɏ']+", text)
        if (m := resolve_prayer(tok)).prayer is not None and not m.fuzzy
    }
    return len(found) >= 2


def _ajaxurl(html: str, url: str) -> str | None:
    m = _AJAXURL_RE.search(html)
    if m:
        return m.group(1).replace("\\/", "/")
    if "admin-ajax.php" in html:
        return urljoin(url, "/wp-admin/admin-ajax.php")
    return None


def _script_sources(soup: BeautifulSoup, base_url: str) -> list[str]:
    """Same-host plugin-ish script URLs worth scanning for the AJAX action."""
    host = urlparse(base_url).netloc
    out: list[str] = []
    for tag in soup.find_all("script", src=True):
        src = urljoin(base_url, tag["src"])
        if urlparse(src).netloc != host:
            continue
        if any(k in src.lower() for k in _SCRIPT_KEYWORDS) and src not in out:
            out.append(src)
    return out[:_MAX_SCRIPTS]


def _candidate_actions(blob: str) -> list[str]:
    """Action names that co-occur with a 'month' parameter in a script blob,
    best-first (those whose own name hints at a month/timetable lead)."""
    actions: list[str] = []
    for m in _ACTION_RE.finditer(blob):
        nearby = blob[m.start() : m.start() + 200]
        if "month" not in nearby.lower():
            continue
        name = m.group(1)
        if name not in actions:
            actions.append(name)
    actions.sort(key=lambda a: any(k in a.lower() for k in _ACTION_KEYWORDS), reverse=True)
    return actions


def _author_endpoint_config(
    ajaxurl: str, action: str, fetcher: Fetcher
) -> SourceConfig | None:
    sep = "&" if "?" in ajaxurl else "?"
    for display in _DISPLAY_VARIANTS:
        template = f"{ajaxurl}{sep}action={action}&month={{month}}{display}"
        try:
            sample_url = template.format(year=2000, month=1)
        except (KeyError, ValueError, IndexError):
            continue
        res = fetcher(sample_url)
        if getattr(res, "error", None) or not getattr(res, "html", None):
            continue
        if getattr(res, "status", 200) >= 400:
            continue
        table = BeautifulSoup(res.html, "lxml").find("table")
        if table is None:
            continue
        grid = author_grid(table, _table_selector(table))
        if grid is None:
            continue
        return SourceConfig(
            shape="html_table",
            grid=grid,
            paging=PagingSpec(mode="url_template", url_template=template),
        )
    return None


def _table_selector(table) -> str | None:
    if table.get("id"):
        return f"table#{table.get('id')}"
    classes = table.get("class") or []
    return "table." + classes[0] if classes else None


class EndpointMonthDetector:
    name = "endpoint_month"

    def detect(
        self, html: str, url: str, *, fetcher: Fetcher | None = None
    ) -> PlatformMatch | None:
        config = self._endpoint_config(html, url, fetcher)
        if config is not None:
            return PlatformMatch(platform=self.name, url=url, requires_js=False, config=config)
        config = self._select_nav_config(html, url)
        if config is not None:
            return PlatformMatch(platform=self.name, url=url, requires_js=True, config=config)
        return None

    def _endpoint_config(self, html: str, url: str, fetcher: Fetcher | None) -> SourceConfig | None:
        if fetcher is None:
            return None
        if not any(marker in html for marker in _ENDPOINT_MARKERS):
            return None
        if not _is_prayer_related(html):
            return None
        ajaxurl = _ajaxurl(html, url)
        if not ajaxurl:
            return None

        soup = BeautifulSoup(html, "lxml")
        blobs = [s.get_text() for s in soup.find_all("script") if s.get_text(strip=True)]
        for src in _script_sources(soup, url):
            res = fetcher(src)
            if not getattr(res, "error", None) and getattr(res, "html", None):
                blobs.append(res.html)

        seen: set[str] = set()
        for blob in blobs:
            for action in _candidate_actions(blob):
                if action in seen:
                    continue
                seen.add(action)
                config = _author_endpoint_config(ajaxurl, action, fetcher)
                if config is not None:
                    return config
        return None

    def _select_nav_config(self, html: str, url: str) -> SourceConfig | None:
        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style", "noscript", "template"]):
            tag.decompose()
        if month_select(soup) is None:
            return None
        paging = detect_month_nav(soup, leaf_tags(soup))
        if paging is None or paging.nav is None or paging.nav.kind != "select":
            return None
        for table in soup.find_all("table"):
            grid = author_grid(table, _table_selector(table))
            if grid is not None:
                return SourceConfig(shape="html_table", grid=grid, paging=paging)
        return None
