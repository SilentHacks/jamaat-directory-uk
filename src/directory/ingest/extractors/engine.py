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
