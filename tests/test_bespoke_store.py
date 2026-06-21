import pytest

from directory.ingest.bespoke_store import load_bespoke, save_module

MODULE_SRC = '''\
from directory.ingest.extractors.bespoke import register_bespoke
from directory.ingest.extractors.engine import ExtractionResult


def extract_persisted(html, *, year, month):
    return ExtractionResult(warnings=["persisted"])


register_bespoke("persisted_key", extract_persisted)
'''


def test_save_module_writes_and_loads(tmp_path):
    path = save_module("acme_masjid", MODULE_SRC, root=tmp_path)

    assert path == tmp_path / "acme_masjid.py"
    assert path.read_text(encoding="utf-8") == MODULE_SRC

    loaded = load_bespoke(tmp_path)
    assert "acme_masjid" in loaded


@pytest.mark.parametrize("bad", ["../escape", "a/b", "a.b", "1bad", "_hidden", ""])
def test_save_module_rejects_unsafe_keys(tmp_path, bad):
    with pytest.raises(ValueError):
        save_module(bad, MODULE_SRC, root=tmp_path)
