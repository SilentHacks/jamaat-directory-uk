import pytest

from directory.domain import Prayer
from directory.ingest.extractors import engine
from directory.ingest.extractors.config_schema import SourceConfig
from directory.ingest.extractors.engine import Cell, ExtractionResult, extract


def test_widget_dispatch_calls_registered_extractor():
    from datetime import date

    def fake(payload, *, year, month):
        return ExtractionResult(
            cells=[Cell(date=date(year, 6, 1), prayer=Prayer.FAJR, kind="jamaah", time="05:00")]
        )

    engine.register_widget("fake", fake)
    cfg = SourceConfig(shape="widget", widget={"platform": "fake"})
    result = extract("{}", cfg, year=2026, month=6)
    assert result.cells[0].time == "05:00"


def test_widget_unknown_platform_raises():
    cfg = SourceConfig(shape="widget", widget={"platform": "nope"})
    with pytest.raises(ValueError):
        extract("{}", cfg, year=2026, month=6)
