from directory.ingest.extractors.config_schema import SourceConfig
from directory.ingest.extractors.platforms import base


def test_detect_platform_returns_first_match():
    class Yes:
        name = "yes"

        def detect(self, html, url, *, fetcher=None):
            return base.PlatformMatch(
                platform="yes",
                url=url,
                requires_js=False,
                config=SourceConfig(shape="rules", rules={"rules": []}),
            )

    class No:
        name = "no"

        def detect(self, html, url, *, fetcher=None):
            return None

    registry = [No(), Yes()]
    match = base.detect_platform("<html></html>", "https://m.example/", registry=registry)
    assert match is not None
    assert match.platform == "yes"


def test_detect_platform_none_when_no_match():
    class No:
        name = "no"

        def detect(self, html, url, *, fetcher=None):
            return None

    assert base.detect_platform("<html></html>", "https://m.example/", registry=[No()]) is None
