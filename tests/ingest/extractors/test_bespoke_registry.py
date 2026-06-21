import pytest

from directory.ingest.extractors.bespoke import (
    BESPOKE_EXTRACTORS,
    get_bespoke,
    load_bespoke,
    register_bespoke,
    save_module,
)
from directory.ingest.extractors.engine import ExtractionResult

MODULE_SRC = '''\
from directory.ingest.extractors.bespoke import register_bespoke
from directory.ingest.extractors.engine import ExtractionResult


def extract_demo(html, *, year, month):
    return ExtractionResult(warnings=[f"demo {year}-{month}"])


register_bespoke("demo_loaded", extract_demo)
'''


def test_register_and_get():
    def fn(html, *, year, month):
        return ExtractionResult()

    register_bespoke("demo_inline", fn)
    assert get_bespoke("demo_inline") is fn
    assert get_bespoke("missing_key") is None


def test_load_bespoke_imports_modules_for_side_effects(tmp_path):
    (tmp_path / "demo.py").write_text(MODULE_SRC, encoding="utf-8")
    (tmp_path / "_skip.py").write_text("raise RuntimeError('should be skipped')", encoding="utf-8")

    loaded = load_bespoke(tmp_path)

    assert loaded == ["demo"]  # dunder/underscore module skipped
    fn = get_bespoke("demo_loaded")
    assert fn is not None
    assert fn("<html/>", year=2026, month=6).warnings == ["demo 2026-6"]


def test_load_bespoke_missing_dir_is_noop(tmp_path):
    assert load_bespoke(tmp_path / "nope") == []


def test_registry_is_the_module_global():
    register_bespoke("demo_global", lambda html, *, year, month: ExtractionResult())
    assert "demo_global" in BESPOKE_EXTRACTORS


def test_save_module_writes_and_loads(tmp_path):
    path = save_module("acme_masjid", MODULE_SRC.replace("demo_loaded", "saved_key"), root=tmp_path)

    assert path == tmp_path / "acme_masjid.py"
    loaded = load_bespoke(tmp_path)
    assert "acme_masjid" in loaded
    assert get_bespoke("saved_key") is not None


@pytest.mark.parametrize("bad", ["../escape", "a/b", "a.b", "1bad", "_hidden", ""])
def test_save_module_rejects_unsafe_keys(tmp_path, bad):
    with pytest.raises(ValueError):
        save_module(bad, MODULE_SRC, root=tmp_path)
