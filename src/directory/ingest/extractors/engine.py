from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date

from bs4 import BeautifulSoup

from directory.domain import Prayer
from directory.ingest.extractors.config_schema import SourceConfig
from directory.ingest.normalize import parse_date, parse_time


@dataclass
class Cell:
    date: date
    prayer: Prayer
    kind: str  # "jamaah" | "begin"
    time: str  # "HH:MM"
    header_seen: str | None = None


@dataclass
class ExtractionResult:
    cells: list[Cell] = field(default_factory=list)
    texts: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _prefer_pm(prayer: Prayer | None) -> bool | None:
    if prayer is None:
        return None
    return prayer != Prayer.FAJR


WidgetExtractor = Callable[..., ExtractionResult]
WIDGET_EXTRACTORS: dict[str, WidgetExtractor] = {}


def register_widget(platform: str, fn: WidgetExtractor) -> None:
    WIDGET_EXTRACTORS[platform] = fn


def extract_html_table(
    html: str, config: SourceConfig, *, year: int, month: int | None = None
) -> ExtractionResult:
    grid = config.grid
    soup = BeautifulSoup(html, "lxml")
    table = soup.select_one(grid.table_selector) if grid.table_selector else soup.find("table")
    if table is None:
        return ExtractionResult(warnings=["table not found"])

    matrix = [
        [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])]
        for tr in table.find_all("tr")
    ]
    if grid.transpose:
        matrix = [list(row) for row in zip(*matrix, strict=False)]

    result = ExtractionResult()
    date_idx = grid.date.index if grid.date else None
    for texts in matrix:
        result.texts.extend(texts)
        if date_idx is None or date_idx >= len(texts):
            continue
        d = parse_date(texts[date_idx], year=year, month=month)
        if d is None:
            continue
        for col in grid.columns:
            if col.index is None or col.index >= len(texts):
                continue
            if col.prayer is None:
                continue
            t = parse_time(texts[col.index], prefer_pm=_prefer_pm(col.prayer))
            if t is None:
                continue
            result.cells.append(
                Cell(date=d, prayer=col.prayer, kind=col.kind, time=t, header_seen=col.header_seen)
            )
    return result


def extract_html_repeated(
    html: str, config: SourceConfig, *, year: int, month: int | None = None
) -> ExtractionResult:
    grid = config.grid
    soup = BeautifulSoup(html, "lxml")
    items = soup.select(grid.row_selector) if grid.row_selector else []
    if not items:
        return ExtractionResult(warnings=["no rows matched row_selector"])

    result = ExtractionResult()
    for item in items:
        dtext = None
        if grid.date and grid.date.selector:
            el = item.select_one(grid.date.selector)
            dtext = el.get_text(" ", strip=True) if el else None
        if dtext:
            result.texts.append(dtext)
        d = parse_date(dtext, year=year, month=month) if dtext else None
        if d is None:
            continue
        for col in grid.columns:
            if not col.selector or col.prayer is None:
                continue
            el = item.select_one(col.selector)
            if el is None:
                continue
            raw = el.get_text(" ", strip=True)
            result.texts.append(raw)
            t = parse_time(raw, prefer_pm=_prefer_pm(col.prayer))
            if t is None:
                continue
            result.cells.append(
                Cell(date=d, prayer=col.prayer, kind=col.kind, time=t, header_seen=col.header_seen)
            )
    return result


def extract(
    html: str, config: SourceConfig, *, year: int, month: int | None = None
) -> ExtractionResult:
    if config.shape == "html_table":
        return extract_html_table(html, config, year=year, month=month)
    if config.shape == "html_repeated":
        return extract_html_repeated(html, config, year=year, month=month)
    if config.shape == "rules":
        return ExtractionResult()
    if config.shape == "widget":
        platform = config.widget.platform
        fn = WIDGET_EXTRACTORS.get(platform)
        if fn is None:
            raise ValueError(f"no widget extractor for platform: {platform!r}")
        return fn(html, year=year, month=month)
    if config.shape == "bespoke":
        from directory.ingest.extractors.bespoke import get_bespoke

        key = config.bespoke.module
        fn = get_bespoke(key)
        if fn is None:
            raise ValueError(f"no bespoke extractor for module: {key!r}")
        try:
            return fn(html, year=year, month=month)
        except Exception as exc:
            return ExtractionResult(warnings=[f"bespoke extractor {key!r} raised: {exc}"])
    raise ValueError(f"unsupported shape: {config.shape!r}")
