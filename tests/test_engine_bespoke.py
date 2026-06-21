from datetime import date

import pytest

from directory.domain import Prayer
from directory.ingest.extractors.bespoke import register_bespoke
from directory.ingest.extractors.config_schema import BespokeSpec, SourceConfig
from directory.ingest.extractors.engine import Cell, ExtractionResult, extract


def test_extract_dispatches_to_registered_bespoke():
    seen = {}

    def fn(html, *, year, month):
        seen["args"] = (html, year, month)
        return ExtractionResult(cells=[Cell(date(2026, 6, 1), Prayer.FAJR, "jamaah", "05:00")])

    register_bespoke("t3_ok", fn)
    cfg = SourceConfig(shape="bespoke", bespoke=BespokeSpec(module="t3_ok"))

    result = extract("<html/>", cfg, year=2026, month=6)

    assert seen["args"] == ("<html/>", 2026, 6)
    assert result.cells[0].time == "05:00"


def test_unknown_bespoke_key_raises():
    cfg = SourceConfig(shape="bespoke", bespoke=BespokeSpec(module="t3_missing"))
    with pytest.raises(ValueError, match="no bespoke extractor"):
        extract("<html/>", cfg, year=2026, month=6)


def test_bespoke_that_raises_yields_warning_not_crash():
    def boom(html, *, year, month):
        raise RuntimeError("kaboom")

    register_bespoke("t3_boom", boom)
    cfg = SourceConfig(shape="bespoke", bespoke=BespokeSpec(module="t3_boom"))

    result = extract("<html/>", cfg, year=2026, month=6)

    assert result.cells == []
    assert any("kaboom" in w for w in result.warnings)
