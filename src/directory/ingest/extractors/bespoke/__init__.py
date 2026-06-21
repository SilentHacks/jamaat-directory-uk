import importlib.util
from collections.abc import Callable
from pathlib import Path

from directory.ingest.extractors.engine import ExtractionResult

BespokeExtractor = Callable[..., ExtractionResult]
BESPOKE_EXTRACTORS: dict[str, BespokeExtractor] = {}


def register_bespoke(key: str, fn: BespokeExtractor) -> None:
    BESPOKE_EXTRACTORS[key] = fn


def get_bespoke(key: str) -> BespokeExtractor | None:
    return BESPOKE_EXTRACTORS.get(key)


def save_module(key: str, code: str, *, root: Path) -> Path:
    """Persist agent-written bespoke extractor ``code`` as ``<root>/<key>.py``.

    ``key`` must be a bare Python identifier not starting with ``_`` so it cannot
    escape ``root`` (no ``/`` or ``..``) and is not skipped by ``load_bespoke``.
    """
    if not key.isidentifier() or key.startswith("_"):
        raise ValueError(f"unsafe bespoke module key: {key!r}")
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{key}.py"
    path.write_text(code, encoding="utf-8")
    return path


def load_bespoke(root: Path) -> list[str]:
    """Import every non-dunder ``*.py`` under ``root`` for its register side effects.

    Returns the module stems loaded. Missing dir → ``[]``. Each module is expected
    to call ``register_bespoke`` at import. Importing executes module-level code, so
    only ever point this at a trusted directory you control.
    """
    loaded: list[str] = []
    if not root.exists():
        return loaded
    for path in sorted(root.glob("*.py")):
        if path.stem.startswith("_"):
            continue
        spec = importlib.util.spec_from_file_location(f"_bespoke_{path.stem}", path)
        if spec is None or spec.loader is None:
            continue
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        loaded.append(path.stem)
    return loaded
