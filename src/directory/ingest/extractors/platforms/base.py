from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from directory.ingest.extractors.config_schema import SourceConfig

# A fetcher matches ingest.fetch.fetch: (url, *, requires_js=…, …) -> FetchResult.
# Endpoint detectors use it to fetch a sample month (e.g. an admin-ajax response)
# so they can author the grid the live page does not itself contain. Detectors
# that read only the handed page ignore it.
Fetcher = Callable[..., object]


@dataclass
class PlatformMatch:
    platform: str
    url: str
    requires_js: bool
    config: SourceConfig


class PlatformDetector(Protocol):
    name: str

    def detect(
        self, html: str, url: str, *, fetcher: Fetcher | None = None
    ) -> PlatformMatch | None: ...


REGISTRY: list[PlatformDetector] = []


def register(detector: PlatformDetector) -> None:
    REGISTRY.append(detector)


def detect_platform(
    html: str,
    url: str,
    *,
    fetcher: Fetcher | None = None,
    registry: list[PlatformDetector] | None = None,
) -> PlatformMatch | None:
    for detector in registry if registry is not None else REGISTRY:
        match = detector.detect(html, url, fetcher=fetcher)
        if match is not None:
            return match
    return None
