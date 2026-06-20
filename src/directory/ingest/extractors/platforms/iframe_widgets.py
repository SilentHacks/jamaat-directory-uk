from dataclasses import dataclass
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from directory.domain import Prayer
from directory.ingest.extractors.config_schema import (
    ColumnSpec,
    DateSpec,
    GridSpec,
    SourceConfig,
)
from directory.ingest.extractors.platforms.base import PlatformMatch, register


@dataclass
class IframeWidgetDetector:
    name: str
    domain: str
    row_selector: str
    date_selector: str
    columns: list[tuple[str, Prayer]]
    requires_js: bool = True

    def detect(self, html: str, url: str) -> PlatformMatch | None:
        soup = BeautifulSoup(html, "lxml")
        for iframe in soup.find_all("iframe"):
            src = iframe.get("src", "")
            if self.domain in src:
                resolved = urljoin(url, src)
                return PlatformMatch(
                    platform=self.name,
                    url=resolved,
                    requires_js=self.requires_js,
                    config=self._config(),
                )
        return None

    def _config(self) -> SourceConfig:
        return SourceConfig(
            shape="html_repeated",
            grid=GridSpec(
                row_selector=self.row_selector,
                date=DateSpec(selector=self.date_selector),
                columns=[
                    ColumnSpec(kind="jamaah", prayer=prayer, selector=sel)
                    for sel, prayer in self.columns
                ],
            ),
        )


MYLOCALMASJID = IframeWidgetDetector(
    name="mylocalmasjid",
    domain="my-masjid.com",
    row_selector="div.prayer-day",
    date_selector=".d",
    columns=[
        (".p-fajr", Prayer.FAJR),
        (".p-dhuhr", Prayer.DHUHR),
        (".p-asr", Prayer.ASR),
        (".p-maghrib", Prayer.MAGHRIB),
        (".p-isha", Prayer.ISHA),
    ],
)

MASJIDBOX = IframeWidgetDetector(
    name="masjidbox",
    domain="masjidbox.com",
    row_selector="div.prayer-day",
    date_selector=".d",
    columns=[
        (".p-fajr", Prayer.FAJR),
        (".p-dhuhr", Prayer.DHUHR),
        (".p-asr", Prayer.ASR),
        (".p-maghrib", Prayer.MAGHRIB),
        (".p-isha", Prayer.ISHA),
    ],
)

register(MYLOCALMASJID)
register(MASJIDBOX)
