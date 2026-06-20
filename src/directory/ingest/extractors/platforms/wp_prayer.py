from bs4 import BeautifulSoup

from directory.domain import DAILY_PRAYERS
from directory.ingest.extractors.config_schema import (
    ColumnSpec,
    DateSpec,
    GridSpec,
    SourceConfig,
)
from directory.ingest.extractors.platforms.base import PlatformMatch, register
from directory.ingest.normalize import resolve_prayer

_SIGNATURE_CLASSES = ("dpt_table", "prayer-times", "mptt-table")


def _signature_table(soup: BeautifulSoup):
    for table in soup.find_all("table"):
        classes = " ".join(table.get("class", []))
        if any(sig in classes for sig in _SIGNATURE_CLASSES):
            return table
    return None


class WpPrayerDetector:
    name = "wp_prayer"

    def detect(self, html: str, url: str) -> PlatformMatch | None:
        soup = BeautifulSoup(html, "lxml")
        table = _signature_table(soup)
        if table is None:
            return None
        header = table.find("tr")
        if header is None:
            return None
        headers = [c.get_text(" ", strip=True) for c in header.find_all(["th", "td"])]

        columns: list[ColumnSpec] = []
        seen: set = set()
        for idx, text in enumerate(headers):
            match = resolve_prayer(text)
            prayer = match.prayer
            # Deterministic detection only: skip fuzzy/low-confidence matches and
            # anything that is not one of the five daily prayers (date/Jumuah columns).
            if prayer is None or match.fuzzy or prayer in seen or prayer not in DAILY_PRAYERS:
                continue
            seen.add(prayer)
            columns.append(
                ColumnSpec(kind="jamaah", prayer=prayer, index=idx, header_seen=text)
            )
        if len(columns) < 3:
            return None

        config = SourceConfig(
            shape="html_table",
            grid=GridSpec(
                table_selector="table." + _matched_class(table),
                transpose=False,
                date=DateSpec(index=0, format="day_only"),
                columns=columns,
            ),
        )
        return PlatformMatch(platform=self.name, url=url, requires_js=False, config=config)


def _matched_class(table) -> str:
    classes = table.get("class", [])
    for sig in _SIGNATURE_CLASSES:
        if sig in classes:
            return sig
    return classes[0] if classes else ""


register(WpPrayerDetector())
