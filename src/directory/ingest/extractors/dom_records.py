"""Extractor for per-day *record streams* — timetables rendered as repeated
day blocks of class-less ``<div>``s rather than an HTML ``<table>``.

Modern site builders (Framer, Wix, React) emit the month as a flat,
document-order stream of leaf elements: a date, then for each prayer a label and
one or two times, with no ``<table>``, no ARIA roles, no stable CSS classes, and
often no Begin/Jamā‘ah headers. This module reads that stream content-first:

1. **Locate** the timetable region by the lowest common ancestor of the date
   leaves (re-derived every run, so it never depends on hashed class names).
2. **Tokenise** the region's leaves in document order into DATE / PRAYER / TIME /
   OTHER tokens (a non-prayer label such as "Shuruq", or an em-dash placeholder).
3. **Segment** by date; within a day, group times under their nearest preceding
   label, so a non-prayer label cleanly absorbs its own orphan time.
4. **Assign roles** by the domain truth that the congregation (jamā‘ah) is at or
   after the begin/adhan: of two times, the later is jamā‘ah, the earlier begin.

It emits standard ``Cell``s, so materialize and the gates are reused unchanged.
"""

from datetime import date

from bs4 import BeautifulSoup, Tag

from directory.domain import DAILY_PRAYERS, Prayer
from directory.ingest.extractors.config_schema import SourceConfig
from directory.ingest.extractors.engine import Cell, ExtractionResult
from directory.ingest.normalize import normalize_token, parse_date, parse_time, resolve_prayer

_WEEKDAYS = frozenset(
    {"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"}
)


def _prefer_pm(prayer: Prayer) -> bool:
    """Disambiguate a bare 12h time: every daily prayer but Fajr is afternoon /
    evening, so an unmarked 1–11 reads as PM (mirrors the engine's column path)."""
    return prayer != Prayer.FAJR


def _looks_time(text: str) -> bool:
    # prefer_pm=True resolves the otherwise-ambiguous bare 12h case, so a value
    # like "6:00" is still recognised as a clock token (its role is fixed later).
    return parse_time(text, prefer_pm=True) is not None


def assign_times(prayer: Prayer, raw_times: list[str]) -> tuple[str | None, str | None]:
    """Resolve a prayer's times into ``(jamaah, begin)``.

    Jamā‘ah (congregation) is always at or after the begin/adhan, so of two
    distinct times the later is jamā‘ah and the earlier begin — independent of the
    order the site lists them. One time → jamā‘ah only; duplicates collapse to one.
    """
    parsed: list[str] = []
    for raw in raw_times:
        t = parse_time(raw, prefer_pm=_prefer_pm(prayer))
        if t is not None:
            parsed.append(t)
    distinct = sorted(set(parsed))
    if not distinct:
        return None, None
    if len(distinct) == 1:
        return distinct[0], None
    return distinct[-1], distinct[0]


def _leaves(node: Tag) -> list[Tag]:
    """Text-bearing leaf elements in document order (no element children)."""
    return [el for el in node.find_all(True) if el.get_text(strip=True) and not el.find(True)]


def _date_leaves(leaves: list[Tag], *, year: int, month: int | None) -> list[Tag]:
    out: list[Tag] = []
    for el in leaves:
        text = el.get_text(" ", strip=True)
        if normalize_token(text) in _WEEKDAYS:
            continue
        if (
            parse_date(text, year=year, month=month) is not None
            and not _looks_time(text)
            and resolve_prayer(text).prayer is None
        ):
            out.append(el)
    return out


def _lca(nodes: list[Tag]) -> Tag | None:
    """Lowest common ancestor of ``nodes`` in the DOM tree."""
    if not nodes:
        return None
    chains = []
    for n in nodes:
        chain = []
        cur: Tag | None = n
        while cur is not None:
            chain.append(cur)
            cur = cur.parent if isinstance(cur.parent, Tag) else None
        chains.append(list(reversed(chain)))  # root → node
    common: Tag | None = None
    for group in zip(*chains, strict=False):
        if all(g is group[0] for g in group):
            common = group[0]
        else:
            break
    return common


def _locate_container(soup: BeautifulSoup, *, year: int, month: int | None) -> Tag | None:
    """The timetable region: the LCA of the date leaves (dates are far more
    timetable-specific than prayer names, so they exclude hero/footer chrome).
    Falls back to the body for a single-day card that carries no date at all."""
    leaves = _leaves(soup)
    dates = _date_leaves(leaves, year=year, month=month)
    if len(dates) >= 2:
        return _lca(dates) or soup.body or soup
    # No usable date axis → a single-day card; the whole body is the region.
    return soup.body or soup


def _classify(text: str, *, year: int, month: int | None):
    """Tokenise one leaf's text into ``(kind, value)`` tokens in order.

    DATE wins for the whole leaf (so "1st Jun" is not split); otherwise each
    whitespace word is a TIME (raw string), a daily PRAYER, or an OTHER label
    (weekday, "Shuruq", em-dash, …) that acts only as a grouping boundary."""
    whole = text.strip()
    if (
        normalize_token(whole) not in _WEEKDAYS
        and parse_date(whole, year=year, month=month) is not None
        and not _looks_time(whole)
        and resolve_prayer(whole).prayer is None
    ):
        return [("DATE", parse_date(whole, year=year, month=month))]
    tokens: list[tuple[str, object]] = []
    for word in whole.split():
        if not word:
            continue
        if _looks_time(word):
            tokens.append(("TIME", word))
            continue
        if normalize_token(word) in _WEEKDAYS:
            tokens.append(("OTHER", word))
            continue
        match = resolve_prayer(word)
        if match.prayer in DAILY_PRAYERS and not match.fuzzy:
            tokens.append(("PRAYER", match.prayer))
        else:
            tokens.append(("OTHER", word))
    return tokens


def _segment(container: Tag, *, year: int, month: int | None, run_day: date) -> list[Cell]:
    """Walk the region's leaves in document order, splitting into day blocks and
    grouping each prayer's times. Tokens before the first date (page chrome) are
    dropped when a date axis exists; with no date axis the whole region is one day
    stamped with ``run_day``."""
    tokens: list[tuple[str, object]] = []
    for el in _leaves(container):
        tokens.extend(_classify(el.get_text(" ", strip=True), year=year, month=month))

    has_dates = any(k == "DATE" for k, _ in tokens)
    cells: list[Cell] = []
    cur_date: date | None = None if has_dates else run_day
    # groups: ordered (prayer_or_None, [raw_time, ...]); a None label is a boundary
    # (e.g. Shuruq) so its times never bleed into the previous prayer.
    groups: list[tuple[Prayer | None, list[str]]] = []

    def flush() -> None:
        if cur_date is None:
            return
        for prayer, times in groups:
            if prayer is None:
                continue
            jamaah, begin = assign_times(prayer, times)
            if jamaah is not None:
                cells.append(Cell(date=cur_date, prayer=prayer, kind="jamaah", time=jamaah))
            if begin is not None:
                cells.append(Cell(date=cur_date, prayer=prayer, kind="begin", time=begin))

    for kind, value in tokens:
        if kind == "DATE":
            flush()
            cur_date = value  # type: ignore[assignment]
            groups = []
        elif cur_date is None:
            continue  # chrome before the first day
        elif kind == "PRAYER":
            groups.append((value, []))  # type: ignore[arg-type]
        elif kind == "OTHER":
            groups.append((None, []))  # boundary
        elif kind == "TIME" and groups:
            groups[-1][1].append(value)  # type: ignore[arg-type]
    flush()
    return cells


def extract_dom_records(
    html: str,
    config: SourceConfig,
    *,
    year: int,
    month: int | None = None,
    today: date | None = None,
) -> ExtractionResult:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "template"]):
        tag.decompose()
    run_day = today or date.today()
    container = _locate_container(soup, year=year, month=month)
    if container is None:
        return ExtractionResult(warnings=["no record container found"])
    cells = _segment(container, year=year, month=month, run_day=run_day)
    texts = [c.time for c in cells if c.time is not None]
    return ExtractionResult(cells=cells, texts=texts)
