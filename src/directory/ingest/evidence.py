"""Structured, deterministic evidence about a fetched page.

The AI authoring funnel used to receive raw candidate HTML and was asked to solve
routing, classification, schema construction and selector authoring in one shot.
This module turns each fetched page into a small, serializable summary — tables,
media links, iframe/widget hints, JS-shell markers and a coarse page class — so the
deterministic enumerator and (later) narrow model prompts can reason over structured
evidence instead of huge HTML blobs.

The page class is a *routing* hint, not final truth: it is intentionally coarse and
conservative. A page is only ever called terminally "no timetable" when it carries
no prayer/time/media/widget/iframe/JS evidence at all.

Also the single home of the JS-shell heuristics (``_page_needs_render`` and friends)
shared with ``discover.py``; they live here so evidence and discovery agree on what
"looks JS-hidden" means without a circular import.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Literal
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from directory.ingest.extractors.nav_detect import detect_month_nav, leaf_tags
from directory.ingest.extractors.tablegrid import (
    combined_header,
    grid_matrix,
    header_depth,
)
from directory.ingest.normalize import parse_date, parse_time, resolve_prayer

PageClass = Literal[
    "structured_html",
    "js_shell",
    "media_only",
    "daily_widget",
    "iframe_or_widget",
    "irrelevant",
    "under_construction",
    "parked_or_spam",
    "empty",
    "unknown",
]


# ── shared JS-shell heuristics (also imported by discover.py) ──────────────────

# Signals that a page is a JS-hydrated shell hiding its prayer data.
_RENDER_MIN_TIMES = 5
_TIME_SCAN_RE = re.compile(r"\b\d{1,2}[:.]\d{2}\b")
_URL_PRAYER_HINTS = ("prayer", "salah", "namaz", "timetable", "time-table", "times")
_JS_MARKERS = (
    "squarespace", "wixstatic", "wix.com", "data-reactroot", 'id="root"',
    "masjidbox", "my-masjid.com", "mawaqit.net",
)


def _visible_text(soup: BeautifulSoup) -> str:
    for tag in soup(["script", "style", "noscript", "template"]):
        tag.decompose()
    return soup.get_text(" ", strip=True)


def _distinct_prayers(text: str) -> int:
    """How many of the five daily prayers are named in ``text``."""
    found: set = set()
    for token in re.findall(r"[A-Za-zÀ-ɏ']+", text):
        match = resolve_prayer(token)
        if match.prayer is not None and not match.fuzzy:
            found.add(match.prayer)
    return len(found)


def _has_empty_prayer_table(soup: BeautifulSoup) -> bool:
    """A table whose header resolves to >=2 prayers but whose body has no data
    rows — the classic hydration skeleton (e.g. Squarespace + Google Sheets)."""
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        prayer_cols = 0
        for cell in (rows[0].find_all(["th", "td"]) if rows else []):
            match = resolve_prayer(cell.get_text(" ", strip=True))
            if match.prayer is not None and not match.fuzzy:
                prayer_cols += 1
        data_rows = [r for r in rows if r.find_all("td")]
        if prayer_cols >= 2 and len(data_rows) <= 1:
            return True
    return False


def _page_needs_render(url: str, html: str) -> bool:
    """True when a page looks prayer-relevant yet lacks enough static data to
    extract — i.e. its timetable is injected by JavaScript. Tuned for recall:
    a false positive only costs one wasted render, while a false negative would
    silently skip a JS site (which the funnel must never do)."""
    soup = BeautifulSoup(html, "lxml")
    text = _visible_text(soup)
    if len(_TIME_SCAN_RE.findall(text)) >= _RENDER_MIN_TIMES:
        return False  # already carries a full timetable statically
    if _has_empty_prayer_table(soup):
        return True
    if _distinct_prayers(text) >= 2:
        return True
    low = html.lower()
    if any(marker in low for marker in _JS_MARKERS):
        url_pray = any(hint in url.lower() for hint in _URL_PRAYER_HINTS)
        if url_pray or _distinct_prayers(text) >= 1:
            return True
    return False


def _js_hints(soup: BeautifulSoup, html: str) -> list[str]:
    """The JS-shell markers actually present on the page, e.g. ``squarespace`` or
    ``empty_prayer_table`` — the evidence a render retry would key off."""
    low = html.lower()
    hints = [marker for marker in _JS_MARKERS if marker in low]
    if _has_empty_prayer_table(soup):
        hints.append("empty_prayer_table")
    return hints


# ── evidence dataclasses ──────────────────────────────────────────────────────


@dataclass
class TableEvidence:
    table_id: str
    selector: str | None
    caption: str | None
    matrix: list[list[str]]
    header_depth: int
    header: list[str]
    body_sample: list[list[str]]
    prayers_named: list[str]
    time_count: int
    date_like_columns: list[int]


@dataclass
class MediaEvidence:
    url: str
    kind: Literal["image", "pdf"]
    text: str
    score: float


@dataclass
class IframeEvidence:
    url: str
    text: str
    provider_hint: str | None


@dataclass
class WidgetHint:
    provider: str
    data_url: str | None
    confidence: float


@dataclass
class NavHint:
    kind: Literal["next", "select"]
    next_selector: str | None = None
    month_select: str | None = None
    year_select: str | None = None
    ready_selector: str | None = None


@dataclass
class PageEvidence:
    url: str
    final_url: str | None
    status: int | None
    score: float
    html_hash: str | None
    title: str | None
    visible_text_sample: str
    page_class: str
    tables: list[TableEvidence] = field(default_factory=list)
    media_links: list[MediaEvidence] = field(default_factory=list)
    iframes: list[IframeEvidence] = field(default_factory=list)
    widget_hints: list[WidgetHint] = field(default_factory=list)
    nav_hints: list[NavHint] = field(default_factory=list)
    js_hints: list[str] = field(default_factory=list)
    terminal_hints: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "PageEvidence":
        """Rebuild a PageEvidence from its serialized form (see
        ``CandidateBundle.load``). Tolerant of missing keys so an older bundle
        written without an ``evidence`` block still round-trips."""
        return cls(
            url=d["url"],
            final_url=d.get("final_url"),
            status=d.get("status"),
            score=d.get("score", 0.0),
            html_hash=d.get("html_hash"),
            title=d.get("title"),
            visible_text_sample=d.get("visible_text_sample", ""),
            page_class=d.get("page_class", "unknown"),
            tables=[TableEvidence(**t) for t in d.get("tables", [])],
            media_links=[MediaEvidence(**m) for m in d.get("media_links", [])],
            iframes=[IframeEvidence(**i) for i in d.get("iframes", [])],
            widget_hints=[WidgetHint(**w) for w in d.get("widget_hints", [])],
            nav_hints=[NavHint(**n) for n in d.get("nav_hints", [])],
            js_hints=list(d.get("js_hints", [])),
            terminal_hints=list(d.get("terminal_hints", [])),
        )


# ── table extraction ──────────────────────────────────────────────────────────

# Caps so a year-long monthly grid does not bloat the stored evidence JSON; the
# model only needs a representative sample to judge orientation and mapping.
_MAX_MATRIX_ROWS = 16
_MAX_MATRIX_COLS = 24
_MAX_BODY_SAMPLE = 6


def _table_selector(table: Tag) -> str | None:
    if table.get("id"):
        return f"table#{table.get('id')}"
    classes = table.get("class") or []
    if classes:
        return "table." + classes[0]
    return None


def _clip(matrix: list[list[str]]) -> list[list[str]]:
    return [row[:_MAX_MATRIX_COLS] for row in matrix[:_MAX_MATRIX_ROWS]]


def _distinct_prayers_named(cells: list[str]) -> list[str]:
    """Distinct daily/Friday prayer names (non-fuzzy) appearing across ``cells``,
    in first-seen order — works whether prayers sit in the header or down a label
    column."""
    out: list[str] = []
    for cell in cells:
        match = resolve_prayer(cell)
        if match.prayer is not None and not match.fuzzy and match.prayer.value not in out:
            out.append(match.prayer.value)
    return out


def _date_like_columns(body: list[list[str]], *, year: int) -> list[int]:
    """Column indices whose body cells mostly parse as a date — the timetable's
    date axis. A column counts as date-like when at least half of its non-empty
    cells resolve via ``parse_date`` (day-only/weekday rows are scoped to month 1
    for the test, which is enough to recognise the shape)."""
    if not body:
        return []
    width = max((len(r) for r in body), default=0)
    out: list[int] = []
    for c in range(width):
        cells = [r[c] for r in body if c < len(r) and r[c].strip()]
        if not cells:
            continue
        hits = sum(1 for cell in cells if parse_date(cell, year=year, month=1) is not None)
        if hits * 2 >= len(cells):
            out.append(c)
    return out


def _table_evidence(table: Tag, table_id: str, *, year: int) -> TableEvidence:
    matrix = grid_matrix(table)
    depth = header_depth(table)
    header = combined_header(matrix, depth) if matrix else []
    body = matrix[depth:]
    caption_el = table.find("caption")
    caption = caption_el.get_text(" ", strip=True) if caption_el else None
    body_cells_flat = [c for row in body for c in row]
    prayers = _distinct_prayers_named(header + body_cells_flat)
    time_count = sum(1 for row in body for c in row if parse_time(c) is not None)
    return TableEvidence(
        table_id=table_id,
        selector=_table_selector(table),
        caption=caption,
        matrix=_clip(matrix),
        header_depth=depth,
        header=header[:_MAX_MATRIX_COLS],
        body_sample=_clip(body)[:_MAX_BODY_SAMPLE],
        prayers_named=prayers,
        time_count=time_count,
        date_like_columns=_date_like_columns(body, year=year),
    )


# ── media extraction ──────────────────────────────────────────────────────────

_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")
_PDF_EXTS = (".pdf",)
_TIMETABLE_HINTS = (
    "prayer", "timetable", "time-table", "time table", "salah", "salat", "namaz",
    "jamaat", "jamaah", "iqamah", "iqamat", "schedule",
)
_MONTH_TOKENS = (
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
)
# A media link this far above 0 is treated as a likely timetable (≥ one strong hint).
MEDIA_TIMETABLE_SCORE = 2.0


def _media_kind(url: str) -> Literal["image", "pdf"] | None:
    low = url.lower().split("?")[0]
    if low.endswith(_PDF_EXTS):
        return "pdf"
    if low.endswith(_IMAGE_EXTS):
        return "image"
    # CMS download endpoints (e.g. WP Download Manager) carry no extension; treat
    # them as PDFs, the common printable-timetable case.
    if "wpdmdl=" in url.lower():
        return "pdf"
    return None


def _media_score(url: str, text: str) -> float:
    blob = f"{url} {text}".lower()
    score = sum(2.0 for hint in _TIMETABLE_HINTS if hint in blob)
    if re.search(r"\b20\d{2}\b", blob):
        score += 1.0
    if any(re.search(rf"\b{m}\b", blob) for m in _MONTH_TOKENS):
        score += 1.0
    return score


def _media_links(soup: BeautifulSoup, base_url: str) -> list[MediaEvidence]:
    seen: set[str] = set()
    out: list[MediaEvidence] = []
    candidates: list[tuple[str, str]] = []
    for a in soup.find_all("a", href=True):
        candidates.append((a["href"], a.get_text(" ", strip=True)))
    for img in soup.find_all("img", src=True):
        candidates.append((img["src"], img.get("alt", "") or ""))
    for href, text in candidates:
        kind = _media_kind(href)
        if kind is None:
            continue
        absolute = urljoin(base_url, href)
        if absolute in seen:
            continue
        seen.add(absolute)
        out.append(
            MediaEvidence(
                url=absolute, kind=kind, text=text, score=_media_score(absolute, text)
            )
        )
    return out


# ── iframe / widget extraction ────────────────────────────────────────────────

_PROVIDER_MARKERS: dict[str, tuple[str, ...]] = {
    "mawaqit": ("mawaqit",),
    "masjidbox": ("masjidbox",),
    "mylocalmasjid": ("mylocalmasjid", "my-local-masjid", "my-masjid", "mymasjid"),
}


def _provider_hint(url: str) -> str | None:
    low = url.lower()
    for provider, markers in _PROVIDER_MARKERS.items():
        if any(marker in low for marker in markers):
            return provider
    return None


def _iframes(soup: BeautifulSoup, base_url: str) -> list[IframeEvidence]:
    out: list[IframeEvidence] = []
    for iframe in soup.find_all("iframe"):
        src = iframe.get("src")
        if not src:
            continue
        absolute = urljoin(base_url, src)
        text = iframe.get("title", "") or iframe.get_text(" ", strip=True)
        out.append(
            IframeEvidence(url=absolute, text=text, provider_hint=_provider_hint(absolute))
        )
    return out


def _widget_hints(iframes: list[IframeEvidence], html: str) -> list[WidgetHint]:
    """Recognised embedded prayer-widget providers. An iframe whose src names the
    provider is high confidence; a bare provider marker elsewhere in the HTML (a
    script embed without an <iframe>) is lower confidence. One hint per provider,
    keeping the strongest."""
    best: dict[str, WidgetHint] = {}

    def _offer(provider: str, data_url: str | None, confidence: float) -> None:
        prev = best.get(provider)
        if prev is None or confidence > prev.confidence:
            best[provider] = WidgetHint(provider, data_url, confidence)

    for iframe in iframes:
        if iframe.provider_hint:
            _offer(iframe.provider_hint, iframe.url, 0.9)
    low = html.lower()
    for provider, markers in _PROVIDER_MARKERS.items():
        if any(marker in low for marker in markers):
            _offer(provider, None, 0.5)
    return list(best.values())


def _nav_hints(soup: BeautifulSoup) -> list[NavHint]:
    paging = detect_month_nav(soup, leaf_tags(soup))
    if paging is None or paging.nav is None:
        return []
    n = paging.nav
    return [
        NavHint(
            kind=n.kind,
            next_selector=n.next_selector,
            month_select=n.month_select,
            year_select=n.year_select,
            ready_selector=n.ready_selector,
        )
    ]


# ── page classification ───────────────────────────────────────────────────────

_UNDER_CONSTRUCTION = (
    "under construction", "coming soon", "site coming soon", "website coming soon",
    "be right back", "under maintenance", "maintenance mode", "launching soon",
    "site is being built", "page is being built",
)
_PARKING_SPAM = (
    "domain is for sale", "buy this domain", "this domain is parked", "domain parking",
    "is for sale", "godaddy", "sedoparking", "namecheap parking",
    "casino", "online betting", "sports betting", "viagra", "payday loan",
    "porn", "xxx",
)
_BUSINESS_VOCAB = (
    "restaurant", "our menu", "book a table", "reservation", "takeaway", "take away",
    "order online", "add to cart", "add to basket", "checkout", "free delivery",
    "estate agent", "for sale by", "car dealership", "law firm",
)
# Below this many chars of visible text the page is effectively empty.
_EMPTY_TEXT_THRESHOLD = 40

# Prayer-specific link hints (no bare "times" — a restaurant's "opening times"
# must not count). An anchor carrying one of these signals the timetable lives on
# a linked page, so the page is never terminal even when it is otherwise bare.
_LINK_PRAYER_HINTS = (
    "prayer", "timetable", "time-table", "salah", "salat", "namaz", "namaaz",
    "jamaah", "jamaat", "iqamah",
)


def _has_prayer_links(soup: BeautifulSoup) -> bool:
    for a in soup.find_all("a", href=True):
        blob = f"{a['href']} {a.get_text(' ', strip=True)}".lower()
        if any(hint in blob for hint in _LINK_PRAYER_HINTS):
            return True
    return False


def _has_phrase(text_low: str, phrases: tuple[str, ...]) -> list[str]:
    return [p for p in phrases if p in text_low]


def _classify(
    *,
    visible_text: str,
    tables: list[TableEvidence],
    media_links: list[MediaEvidence],
    iframes: list[IframeEvidence],
    widget_hints: list[WidgetHint],
    nav_hints: list[NavHint],
    js_hints: list[str],
    has_prayer_links: bool,
    url: str,
) -> tuple[PageClass, list[str]]:
    """Coarse, conservative routing class for a page plus the terminal hint phrases
    found. Positive (timetable-bearing) classes win first; a terminal class is only
    returned when the page carries no prayer/time/media/widget/iframe/JS signal at
    all."""
    text_low = visible_text.lower()
    n_text = len(visible_text.strip())

    prayer_in_tables = any(t.prayers_named for t in tables)
    timed_table = any(t.prayers_named and t.time_count for t in tables)
    prayer_signal = prayer_in_tables or _distinct_prayers(visible_text) >= 2
    time_signal = (
        any(t.time_count for t in tables)
        or len(_TIME_SCAN_RE.findall(visible_text)) >= 1
    )
    media_signal = any(m.score >= MEDIA_TIMETABLE_SCORE for m in media_links)
    widget_signal = bool(widget_hints) or bool(iframes) or bool(nav_hints)

    # Positive classes first.
    if timed_table:
        return "structured_html", []
    if widget_signal:
        return "iframe_or_widget", []
    if media_signal:
        return "media_only", []
    if js_hints and (prayer_signal or any(h in url.lower() for h in _URL_PRAYER_HINTS)):
        return "js_shell", []
    if prayer_signal and time_signal:
        return "structured_html", []
    if prayer_signal:
        # Prayer names but no full timetable: a thin daily widget or a JS-hidden
        # grid. Non-terminal — leave it for rendering/enumeration/the model.
        return "daily_widget", []

    # Prayer-keyword links → the timetable likely lives on a linked page (which may
    # have been unreachable at fetch time). Ambiguous, never terminal.
    if has_prayer_links:
        return "unknown", []

    # Terminal classes: reached only with no timetable signal whatsoever.
    construction = _has_phrase(text_low, _UNDER_CONSTRUCTION)
    if construction:
        return "under_construction", construction
    spam = _has_phrase(text_low, _PARKING_SPAM)
    if spam:
        return "parked_or_spam", spam
    if n_text < _EMPTY_TEXT_THRESHOLD:
        return "empty", []
    business = _has_phrase(text_low, _BUSINESS_VOCAB)
    if business:
        return "irrelevant", business
    return "unknown", []


def build_page_evidence(
    html: str,
    url: str,
    *,
    final_url: str | None = None,
    status: int | None = None,
    html_hash: str | None = None,
    today: date | None = None,
) -> PageEvidence:
    """Parse ``html`` once into a structured, serializable PageEvidence summary."""
    soup = BeautifulSoup(html, "lxml")
    year = (today or date.today()).year
    visible = _visible_text(BeautifulSoup(html, "lxml"))  # fresh soup; _visible_text mutates
    title_el = soup.find("title")
    title = title_el.get_text(" ", strip=True) if title_el else None

    tables = [
        _table_evidence(t, f"table_{i}", year=year)
        for i, t in enumerate(soup.find_all("table"))
    ]
    media = _media_links(soup, url)
    iframes = _iframes(soup, url)
    widgets = _widget_hints(iframes, html)
    nav = _nav_hints(soup)
    js = _js_hints(BeautifulSoup(html, "lxml"), html)

    page_class, terminal_hints = _classify(
        visible_text=visible,
        tables=tables,
        media_links=media,
        iframes=iframes,
        widget_hints=widgets,
        nav_hints=nav,
        js_hints=js,
        has_prayer_links=_has_prayer_links(soup),
        url=url,
    )

    return PageEvidence(
        url=url,
        final_url=final_url,
        status=status,
        score=_score_text(visible),
        html_hash=html_hash,
        title=title,
        visible_text_sample=visible[:600],
        page_class=page_class,
        tables=tables,
        media_links=media,
        iframes=iframes,
        widget_hints=widgets,
        nav_hints=nav,
        js_hints=js,
        terminal_hints=terminal_hints,
    )


def classify_page(html: str, url: str, *, today: date | None = None) -> str:
    """The coarse routing class of a page (see ``PageClass``)."""
    return build_page_evidence(html, url, today=today).page_class


_KEYWORDS = ("prayer", "timetable", "salah", "salat", "namaz", "jamaah", "iqamah", "times")


def _score_text(text: str) -> float:
    low = text.lower()
    hits = sum(low.count(k) for k in _KEYWORDS)
    return hits + (5.0 if ":" in text else 0.0)


# ── terminal routing for discovery ────────────────────────────────────────────

_TERMINAL_CLASSES = frozenset(
    {"empty", "under_construction", "parked_or_spam", "irrelevant"}
)
# Class → (last_status detail, human reason). Used when persisting a terminal
# no_timetable triage from discovery.
_TERMINAL_REASON: dict[str, tuple[str, str]] = {
    "under_construction": ("under_construction", "site appears to be under construction"),
    "parked_or_spam": ("parked_or_spam", "domain appears parked or spam"),
    "irrelevant": ("wrong_site", "site content is unrelated to a mosque timetable"),
    "empty": ("empty_page", "page has no usable content"),
}
# Tie-break order when pages disagree: prefer the most specific/actionable.
_TERMINAL_PRIORITY = ("under_construction", "parked_or_spam", "irrelevant", "empty")


def terminal_no_timetable(evidences: list[PageEvidence]) -> tuple[str, str] | None:
    """When *every* usable page is conclusively not a timetable — under
    construction, parked, wrong site or empty — and no page carries any
    media/widget/iframe/nav/prayer-table/JS evidence, return the
    ``(last_status, last_error)`` to record. Returns None for any ambiguity, so an
    uncertain source still flows to the candidate bundle / model. Conservative by
    construction: a single non-terminal page aborts the whole verdict."""
    if not evidences:
        return None
    for e in evidences:
        if any(m.score >= MEDIA_TIMETABLE_SCORE for m in e.media_links):
            return None
        if e.widget_hints or e.iframes or e.nav_hints:
            return None
        if any(t.prayers_named for t in e.tables):
            return None
        if e.js_hints:
            return None
        if e.page_class not in _TERMINAL_CLASSES:
            return None
    classes = [e.page_class for e in evidences]
    chosen = min(
        (c for c in classes),
        key=lambda c: _TERMINAL_PRIORITY.index(c) if c in _TERMINAL_PRIORITY else 99,
    )
    return _TERMINAL_REASON[chosen]
