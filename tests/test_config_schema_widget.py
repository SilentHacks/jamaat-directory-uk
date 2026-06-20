import pytest

from directory.ingest.extractors.config_schema import SourceConfig


def test_widget_requires_widget_block():
    with pytest.raises(ValueError):
        SourceConfig.from_json('{"shape":"widget"}')


def test_widget_roundtrips():
    cfg = SourceConfig.from_json(
        '{"shape":"widget","widget":{"platform":"mawaqit","data_url":"https://x/api"}}'
    )
    assert cfg.shape == "widget"
    assert cfg.widget.platform == "mawaqit"
    assert SourceConfig.from_json(cfg.to_json()).widget.data_url == "https://x/api"
