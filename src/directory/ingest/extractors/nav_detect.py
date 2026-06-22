"""Detect how a JS calendar exposes its other months.

A single-month view (a month caption plus a month ``<select>`` or a forward
control) can be walked across the horizon by the ``render_nav`` pager. These
helpers turn that DOM evidence into a ``PagingSpec``. Shared by the ``dom_records``
detector (per-day record streams) and the generic ``endpoint_month`` sniffer
(a ``<select>``-driven ``<table>`` grid).
"""

from bs4 import BeautifulSoup, Tag

from directory.ingest.extractors.config_schema import NavSpec, PagingSpec
from directory.ingest.normalize import month_from_text, normalize_token

# Single-glyph / word controls that step a JS calendar forward one month.
_NEXT_TOKENS = ("›", "»", "→", "❯", "＞", ">", "next", "next month")
_MONTH_OPTIONS_MIN = 12


def month_caption(leaves: list[Tag]) -> bool:
    """True when a leaf is a bare month label (optionally with a year), e.g.
    "June 2026" — the caption of a single-month calendar view."""
    return any(month_from_text(el.get_text(" ", strip=True)) is not None for el in leaves)


def month_select(soup: BeautifulSoup) -> str | None:
    """CSS selector for a ``<select>`` whose options are month names, if any."""
    for sel in soup.find_all("select"):
        months = sum(
            1
            for opt in sel.find_all("option")
            if month_from_text(opt.get_text(" ", strip=True)) is not None
        )
        if months >= _MONTH_OPTIONS_MIN:
            if sel.get("id"):
                return f"select#{sel.get('id')}"
            if sel.get("name"):
                return f"select[name='{sel.get('name')}']"
            classes = sel.get("class") or []
            if classes:
                return "select." + classes[0]
            return "select"
    return None


def next_control(leaves: list[Tag]) -> str | None:
    """A Playwright ``text=`` selector for a forward (next-month) control."""
    for el in leaves:
        token = normalize_token(el.get_text(" ", strip=True))
        raw = el.get_text(" ", strip=True)
        if raw in _NEXT_TOKENS or token in _NEXT_TOKENS:
            return f"text={raw}"
    return None


def detect_month_nav(soup: BeautifulSoup, leaves: list[Tag]) -> PagingSpec | None:
    """A ``render_nav`` paging spec when the page is a single-month view with a
    month control. Prefers a month ``<select>`` (precise) over a forward glyph."""
    if not month_caption(leaves):
        return None
    sel = month_select(soup)
    if sel is not None:
        return PagingSpec(mode="render_nav", nav=NavSpec(kind="select", month_select=sel))
    nxt = next_control(leaves)
    if nxt is not None:
        return PagingSpec(mode="render_nav", nav=NavSpec(kind="next", next_selector=nxt))
    return None


def leaf_tags(soup: BeautifulSoup) -> list[Tag]:
    """The text-bearing leaf elements of a (script/style-stripped) soup — the
    candidates for month captions and forward controls."""
    return [el for el in soup.find_all(True) if el.get_text(strip=True) and not el.find(True)]
