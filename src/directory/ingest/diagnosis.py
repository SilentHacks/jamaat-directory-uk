"""Dry-run inspection of a candidate source (Phase 8).

``diagnose_candidate`` shows what authoring *would* do — page classes, the
deterministic config candidates and their in-memory verify results, and the
narrow prompt kind a model would receive — without persisting anything or calling
a model. Backs the ``directory inspect-candidate`` CLI command.
"""

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from directory.ingest.config_enumerator import enumerate_candidates
from directory.ingest.discover import CandidateBundle
from directory.ingest.fetch import fetch
from directory.ingest.prompt import PromptKind, route_prompt_kind
from directory.ingest.verify import verify_candidate


@dataclass
class PageDiagnosis:
    url: str
    page_class: str
    n_tables: int
    n_media: int
    n_widgets: int
    n_iframes: int
    js_hints: list[str]
    terminal_hints: list[str]


@dataclass
class CandidateDiagnosis:
    source: str
    reason: str
    ok: bool
    triage_status: str
    rows_count: int
    reasons: list[str]


@dataclass
class DiagnoseReport:
    """A dry-run picture of what authoring *would* do for one candidate source: the
    page classes, the deterministic config candidates and their in-memory verify
    results, whether the deterministic pass recovers a config, and — if not — the
    narrow prompt kind a model would receive. No DB writes, no model calls."""

    mosque_id: str
    found_bundle: bool
    pages: list[PageDiagnosis]
    candidates: list[CandidateDiagnosis]
    deterministic_recovered: bool
    prompt_kind: str


def diagnose_candidate(
    engine,
    mosque_id: str,
    *,
    candidate_root: Path,
    today: date | None = None,
    horizon_days: int = 60,
    fetcher=fetch,
    renderer=None,
    nav_renderer=None,
) -> DiagnoseReport:
    """Inspect a candidate source without authoring it. Loads the bundle, summarizes
    each page's evidence, enumerates deterministic config candidates and verifies each
    in memory, and reports the prompt kind a model would get if the deterministic pass
    cannot recover. Pure diagnosis — nothing is persisted and no model is called."""
    bundle = CandidateBundle.load(mosque_id, candidate_root)
    if bundle is None or not bundle.candidates:
        return DiagnoseReport(mosque_id, False, [], [], False, PromptKind.NONE)

    evidence = bundle.evidence
    pages = [
        PageDiagnosis(
            url=p.url, page_class=p.page_class, n_tables=len(p.tables),
            n_media=len(p.media_links), n_widgets=len(p.widget_hints),
            n_iframes=len(p.iframes), js_hints=list(p.js_hints),
            terminal_hints=list(p.terminal_hints),
        )
        for p in evidence
    ]

    candidates: list[CandidateDiagnosis] = []
    recovered = False
    for cand in enumerate_candidates(evidence):
        attempt = verify_candidate(
            cand, today=today, horizon_days=horizon_days, fetcher=fetcher,
            renderer=renderer, nav_renderer=nav_renderer,
        )
        candidates.append(
            CandidateDiagnosis(
                source=cand.source, reason=cand.reason, ok=attempt.ok,
                triage_status=attempt.triage_status, rows_count=attempt.rows_count,
                reasons=attempt.reasons,
            )
        )
        recovered = recovered or attempt.ok

    prompt_kind = PromptKind.NONE if recovered else route_prompt_kind(evidence)
    return DiagnoseReport(mosque_id, True, pages, candidates, recovered, prompt_kind)
