from pathlib import Path

from directory.ingest.extractors.bespoke import load_bespoke

__all__ = ["load_bespoke", "save_module"]


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
