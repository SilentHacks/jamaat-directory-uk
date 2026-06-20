from dataclasses import dataclass
from typing import Protocol

from directory.ingest.extractors.config_schema import SourceConfig


@dataclass
class PlatformMatch:
    platform: str
    url: str
    requires_js: bool
    config: SourceConfig


class PlatformDetector(Protocol):
    name: str

    def detect(self, html: str, url: str) -> PlatformMatch | None: ...


REGISTRY: list[PlatformDetector] = []


def register(detector: PlatformDetector) -> None:
    REGISTRY.append(detector)


def detect_platform(
    html: str, url: str, *, registry: list[PlatformDetector] | None = None
) -> PlatformMatch | None:
    for detector in registry if registry is not None else REGISTRY:
        match = detector.detect(html, url)
        if match is not None:
            return match
    return None
