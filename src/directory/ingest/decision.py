"""Parse a harness reply into an ``AuthorDecision`` and build a ``SourceConfig``
from the compact ``table_mapping`` envelope.

This is the pure parsing/building layer of authoring: no DB, no model, no
verification. ``author.py`` consumes it, verifies the resulting config in memory,
and decides what to persist.
"""

import json
from dataclasses import dataclass

from directory.ingest.evidence import PageEvidence
from directory.ingest.extractors.config_schema import (
    ColumnSpec,
    MediaSpec,
    SourceConfig,
)
from directory.ingest.extractors.table_orientations import HORIZONTAL_MULTIDAY, grid_for
from directory.ingest.jsonscan import first_json_object

# Model outcomes that terminate authoring with no extractable timetable; both land
# the source on triage_status="no_timetable" (the last_status detail distinguishes
# them). See gates/discovery for the deterministic counterparts.
TERMINAL_OUTCOMES = frozenset({"no_timetable", "wrong_site"})
# wrong_site keeps its own last_status so a misrouted website is distinguishable
# from a genuine "this mosque publishes no timetable".
TERMINAL_LAST_STATUS = {"no_timetable": "no_timetable", "wrong_site": "wrong_site"}

# Fields a model may set on a table_mapping column; anything else is dropped before
# building the ColumnSpec (so a stray key cannot raise a schema error).
_COLUMN_FIELDS = frozenset(
    {"kind", "prayer", "index", "time_index", "selector", "header_seen", "value_kind",
     "base_prayer"}
)


def extract_json(text: str) -> str | None:
    """Return the first balanced top-level JSON object in ``text``, or None."""
    return first_json_object(text)


@dataclass
class AuthorDecision:
    """A parsed harness reply. ``outcome`` routes what happens next:
    - ``config``: ``config`` (+ optional ``module_code``) is verified and persisted.
    - ``table_mapping``: a compact table column mapping; local code builds the config.
    - ``media``: ``config`` is an image/pdf media config to defer.
    - ``no_timetable`` / ``wrong_site``: terminal — record and stop, no escalation.
    - ``unknown``: the model could not decide; escalate to the next stage.
    """

    outcome: str
    config: SourceConfig | None = None
    url: str | None = None
    module_code: str | None = None
    reason: str | None = None
    # table_mapping fields:
    table_id: str | None = None
    orientation: str | None = None
    date_index: int | None = None
    label_index: int | None = None
    columns: list[dict] | None = None


def parse_decision(raw: str, default_url: str) -> AuthorDecision:
    """Parse a harness reply into an AuthorDecision.

    Accepts the historical config envelopes — ``{"url":..., "config": {...},
    "module_code": "..."}`` or a bare config object — and the narrow decision
    envelopes ``{"outcome": "table_mapping"|"media"|"no_timetable"|"wrong_site"
    |"unknown", ...}``. Raises ValueError (incl. pydantic ValidationError) on
    anything invalid.
    """
    obj = extract_json(raw)
    if obj is None:
        raise ValueError("no JSON object in harness output")
    data = json.loads(obj)
    if not isinstance(data, dict):
        raise ValueError("harness output is not a JSON object")

    outcome = data.get("outcome")

    if outcome in TERMINAL_OUTCOMES:
        return AuthorDecision(
            outcome=outcome, reason=data.get("reason"), url=data.get("url") or default_url
        )
    if outcome == "unknown":
        return AuthorDecision(
            outcome="unknown", reason=data.get("reason"), url=data.get("url") or default_url
        )
    if outcome == "media":
        kind = data.get("kind")
        media_url = data.get("url")
        if kind not in {"image", "pdf"} or not media_url:
            raise ValueError("media decision requires kind 'image'|'pdf' and a url")
        return AuthorDecision(
            outcome="media",
            config=SourceConfig(shape=kind, media=MediaSpec(url=media_url)),
            url=data.get("page_url") or default_url,
            reason=data.get("reason"),
        )
    if outcome == "table_mapping":
        return AuthorDecision(
            outcome="table_mapping",
            url=data.get("url") or default_url,
            table_id=data.get("table_id"),
            orientation=data.get("orientation"),
            date_index=data.get("date_index"),
            label_index=data.get("label_index"),
            columns=data.get("columns"),
        )

    # Config envelope or bare config (back-compat).
    module_code: str | None = None
    if "config" in data:
        cfg_obj = data["config"]
        url = data.get("url") or default_url
        module_code = data.get("module_code")
    else:
        cfg_obj = data
        url = default_url
    return AuthorDecision(
        outcome="config",
        config=SourceConfig.model_validate(cfg_obj),
        url=url,
        module_code=module_code,
    )


def _selector_for_table(table_id: str | None, evidence: list[PageEvidence]) -> str | None:
    for page in evidence:
        for t in page.tables:
            if t.table_id == table_id:
                return t.selector
    return None


def config_from_table_mapping(
    decision: AuthorDecision, evidence: list[PageEvidence]
) -> SourceConfig:
    """Build an ``html_table`` SourceConfig from a model's compact table_mapping,
    resolving the table's CSS selector from the evidence by ``table_id`` and
    delegating the orientation→grid mapping to ``grid_for`` (the same builder the
    deterministic detectors use)."""
    columns = [
        ColumnSpec(**{k: v for k, v in (c or {}).items() if k in _COLUMN_FIELDS})
        for c in (decision.columns or [])
    ]
    if not columns:
        raise ValueError("table_mapping has no columns")
    grid = grid_for(
        decision.orientation or HORIZONTAL_MULTIDAY,
        columns=columns,
        selector=_selector_for_table(decision.table_id, evidence),
        date_index=decision.date_index,
        label_index=decision.label_index,
    )
    return SourceConfig(shape="html_table", grid=grid)
