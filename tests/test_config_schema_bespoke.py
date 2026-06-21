import pytest

from directory.ingest.extractors.config_schema import BespokeSpec, SourceConfig


def test_bespoke_config_round_trips_json():
    cfg = SourceConfig(shape="bespoke", bespoke=BespokeSpec(module="acme"))
    raw = cfg.to_json()
    assert '"shape":"bespoke"' in raw
    again = SourceConfig.from_json(raw)
    assert again.shape == "bespoke"
    assert again.bespoke.module == "acme"


def test_bespoke_without_spec_is_rejected():
    with pytest.raises(ValueError):
        SourceConfig(shape="bespoke")
