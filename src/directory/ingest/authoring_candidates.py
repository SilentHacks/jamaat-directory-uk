"""A speculative authoring candidate.

A ``ConfigCandidate`` is a ``SourceConfig`` produced either by code (the
deterministic enumerator or a platform detector) or by a model, tagged with where
it came from and why. Candidates are *verified in memory* (see ``verify.py``)
before any of them is written to the DB, so a speculative config never corrupts a
source's stored state.
"""

from dataclasses import dataclass

from directory.ingest.extractors.config_schema import SourceConfig

# Source prefix for a candidate that came from a registered platform detector.
_PLATFORM_PREFIX = "platform:"


@dataclass
class ConfigCandidate:
    url: str
    config: SourceConfig
    # Provenance, e.g. "platform:generic_table", "enumerator:table_horizontal_multiday",
    # "enumerator:media_pdf", "model:table_repair", "model:media_choice".
    source: str
    reason: str
    confidence: float
    requires_js: bool = False

    @property
    def platform(self) -> str | None:
        """The detector name when this candidate came from a platform detector
        (``source`` of the form ``platform:<name>``), else None."""
        if self.source.startswith(_PLATFORM_PREFIX):
            return self.source[len(_PLATFORM_PREFIX):]
        return None
