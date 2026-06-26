"""In-memory verification of speculative configs.

A ``ConfigCandidate`` is run through the same extract → materialize → gate path the
daily extractor uses (``runner.evaluate_config``) but with NO DB writes, yielding a
``VerifyAttempt``. Only a verified candidate is then committed via
``persist_verified_candidate`` — so a bad model output or a low-quality enumerator
guess can never overwrite a source's stored config (the Phase 4 guarantee).
"""

from dataclasses import dataclass
from datetime import date

from directory import repository as repo
from directory.db import session_scope
from directory.ingest.authoring_candidates import ConfigCandidate
from directory.ingest.fetch import fetch
from directory.ingest.runner import (
    ExtractionEvaluation,
    ExtractOutcome,
    evaluate_config,
    persist_evaluation,
)


@dataclass
class VerifyAttempt:
    candidate: ConfigCandidate
    ok: bool
    triage_status: str  # authored|review|deferred_media|needs_reauthor
    lane: str
    rows_count: int
    confidence: float
    reasons: list[str]
    flags: list[str]
    # The full evaluation, retained so a verified attempt can be persisted from the
    # already-computed rows/flags/hash without a second fetch.
    evaluation: ExtractionEvaluation
    error: str | None = None


def verify_candidate(
    candidate: ConfigCandidate,
    *,
    today: date | None = None,
    horizon_days: int = 60,
    fetcher=fetch,
    renderer=None,
    nav_renderer=None,
) -> VerifyAttempt:
    """Verify a candidate config in memory. Mirrors ``extract_source`` minus the DB
    writes: ``ok`` means the config produced a usable, gate-passing (or deferrable
    media) result."""
    today = today or date.today()
    ev = evaluate_config(
        candidate.config, candidate.url, today=today, horizon_days=horizon_days,
        requires_js=candidate.requires_js, fetcher=fetcher, renderer=renderer,
        nav_renderer=nav_renderer,
    )
    reasons = list(ev.gate.reasons) if ev.gate else ([ev.error] if ev.error else [])
    return VerifyAttempt(
        candidate=candidate, ok=ev.ok, triage_status=ev.triage_status, lane=ev.lane,
        rows_count=len(ev.rows), confidence=ev.confidence, reasons=reasons,
        flags=list(ev.flags), evaluation=ev, error=ev.error,
    )


def persist_verified_candidate(
    engine,
    mosque_id: str,
    attempt: VerifyAttempt,
    *,
    authored_by: str | None,
) -> ExtractOutcome:
    """Commit a verified candidate: write its config, then land the already-computed
    evaluation (occurrences + status). Must only be called for ``attempt.ok`` — a
    rejected attempt is never persisted, so a source's config is only ever replaced
    by something that verified."""
    cand = attempt.candidate
    config = cand.config
    # Write the chosen config first (the source row may not exist yet, or may hold
    # a stale config); persist_evaluation then fills status + occurrences from the
    # evaluation verify already computed — no re-fetch, no speculative bad write.
    with session_scope(engine, write=True) as s:
        repo.create_or_update_source(
            s, source_id=mosque_id, mosque_id=mosque_id, url=cand.url,
            platform=cand.platform, shape=config.shape, config=config.to_json(),
            requires_js=cand.requires_js, triage_status="candidate",
        )
    return persist_evaluation(
        engine, mosque_id, mosque_id, config, attempt.evaluation,
        accept_review=False, authored_by=authored_by,
    )
