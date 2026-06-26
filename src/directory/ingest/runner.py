from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from directory import repository as repo
from directory.db import session_scope
from directory.ingest.extractors.config_schema import SourceConfig
from directory.ingest.extractors.engine import ExtractionResult
from directory.ingest.fetch import fetch, html_hash
from directory.ingest.gates import GateResult, jumuah_failure, run_gates
from directory.ingest.materialize import OccurrenceRow, materialize
from directory.ingest.pager import (
    collect_documents,
    extract_documents,
    months_in_horizon,
)

PARTIAL_HORIZON = "partial_horizon"
DEFERRED_MEDIA = "deferred_media"
# Shapes whose timetable is an image/PDF the engine does not parse: classified
# and recorded here, daily extraction deferred to a later phase.
_MEDIA_SHAPES = {"image", "pdf"}


@dataclass
class ExtractOutcome:
    source_id: str
    ok: bool
    rows_written: int
    lane: str
    triage_status: str
    error: str | None = None


@dataclass
class ExtractionEvaluation:
    """The result of running a config through extract → materialize → gates
    *without* touching the DB. Both ``extract_source`` (which persists it) and
    ``verify_candidate`` (which does not) build one of these via ``evaluate_config``,
    so the fetch/extract/materialize/gate path lives in exactly one place.

    ``triage_status`` is the status a default (non-accept-review) persist would
    land: ``authored`` (auto_accept), ``review``, ``deferred_media`` (media), or
    ``needs_reauthor`` (auto_reject / fetch-empty / implausible media jumuah).
    ``ok`` is True when the config produced a usable, non-rejected result."""

    ok: bool
    lane: str  # gate lane ("auto_accept"|"review"|"auto_reject"), or "deferred" for media
    triage_status: str
    rows: list[OccurrenceRow]
    gate: GateResult | None
    flags: list[str]
    docs_count: int
    expected_docs_count: int
    confidence: float
    html_hash: str | None
    media: bool
    error: str | None = None


def _reauthor(engine, source_id, error) -> ExtractOutcome:
    with session_scope(engine, write=True) as s:
        repo.set_source_state(
            s, source_id, triage_status="needs_reauthor", last_status="error", last_error=error
        )
        repo.record_extractor_run(s, source_id, ok=False, rows_written=0, error=error)
    return ExtractOutcome(source_id, False, 0, "auto_reject", "needs_reauthor", error)


def _evaluate_media(
    config: SourceConfig, *, today: date, horizon_days: int
) -> ExtractionEvaluation:
    """Evaluate an image/PDF source without fetching/parsing the media: materialize
    any structured Jumu'ah and require it to be plausible. The media itself is read
    in a later phase; the daily timetable is deferred."""
    horizon_end = today + timedelta(days=horizon_days)
    rows = materialize(
        ExtractionResult(), config, horizon_start=today, horizon_end=horizon_end
    )
    jfail = jumuah_failure(rows)
    if jfail is not None:
        return ExtractionEvaluation(
            ok=False, lane="auto_reject", triage_status="needs_reauthor", rows=[],
            gate=None, flags=[], docs_count=0, expected_docs_count=0, confidence=0.0,
            html_hash=None, media=True, error=f"deferred media jumuah: {jfail}",
        )
    return ExtractionEvaluation(
        ok=True, lane="deferred", triage_status=DEFERRED_MEDIA, rows=rows, gate=None,
        flags=[], docs_count=0, expected_docs_count=0, confidence=0.0, html_hash=None,
        media=True, error=None,
    )


def evaluate_config(
    config: SourceConfig,
    url: str | None,
    *,
    today: date | None = None,
    horizon_days: int = 60,
    requires_js: bool = False,
    fetcher=fetch,
    renderer=None,
    nav_renderer=None,
) -> ExtractionEvaluation:
    """Run a config end-to-end (collect → extract → materialize → gates) and report
    what would happen, with no DB writes. The single source of truth for "is this
    config good?", shared by the daily extractor and the in-memory verifier."""
    today = today or date.today()

    if config.shape in _MEDIA_SHAPES:
        return _evaluate_media(config, today=today, horizon_days=horizon_days)

    horizon_end = today + timedelta(days=horizon_days)
    expected = len(months_in_horizon(today, horizon_days))
    docs, err = collect_documents(
        config, url, today=today, horizon_days=horizon_days, requires_js=requires_js,
        fetcher=fetcher, renderer=renderer, nav_renderer=nav_renderer,
    )
    if err or not docs:
        return ExtractionEvaluation(
            ok=False, lane="auto_reject", triage_status="needs_reauthor", rows=[],
            gate=None, flags=[], docs_count=0, expected_docs_count=expected,
            confidence=0.0, html_hash=None, media=False, error=err or "empty body",
        )

    result = extract_documents(docs, config, today=today)
    combined_html = "\n".join(d.html for d in docs)
    rows = materialize(result, config, horizon_start=today, horizon_end=horizon_end)
    gate = run_gates(config, result, rows, html_text=combined_html)

    # A short month set (a future month not yet published) is tolerated, but
    # flagged so a chronically partial source is visible.
    flags = list(gate.flags)
    if len(docs) < expected:
        flags.append(PARTIAL_HORIZON)

    if gate.lane == "auto_accept":
        triage, ok = "authored", True
    elif gate.lane == "review":
        triage, ok = "review", True
    else:
        triage, ok = "needs_reauthor", False

    return ExtractionEvaluation(
        ok=ok, lane=gate.lane, triage_status=triage, rows=rows, gate=gate, flags=flags,
        docs_count=len(docs), expected_docs_count=expected, confidence=gate.confidence,
        html_hash=html_hash(combined_html), media=False,
        error=None if ok else "; ".join(gate.reasons),
    )


def persist_evaluation(
    engine,
    source_id: str,
    mosque_id: str,
    config: SourceConfig,
    ev: ExtractionEvaluation,
    *,
    accept_review: bool = False,
    authored_by: str | None = None,
) -> ExtractOutcome:
    """Write the outcome of an evaluation. Activates auto_accept (and review when
    ``accept_review``) by replacing occurrences and marking ``authored``; holds a
    plain review; defers media; and re-authors a rejection — never wiping the
    last-known occurrences on a failure (the drift guard)."""
    now = datetime.now(tz=UTC).isoformat(timespec="seconds")
    authored_at = now if authored_by else None

    if ev.media:
        if not ev.ok:
            return _reauthor(engine, source_id, ev.error or "media rejected")
        reason = f"timetable is {config.shape}; daily extraction deferred ({config.media.url})"
        with session_scope(engine, write=True) as s:
            n = repo.replace_source_occurrences(s, source_id, mosque_id, ev.rows)
            repo.set_source_state(
                s, source_id, triage_status=DEFERRED_MEDIA, confidence=0.0,
                review_reason=reason, last_status=DEFERRED_MEDIA, last_fetched_at=now,
                authored_by=authored_by, authored_at=authored_at,
            )
            repo.record_extractor_run(s, source_id, ok=True, rows_written=n)
        return ExtractOutcome(source_id, True, n, "deferred", DEFERRED_MEDIA)

    if not ev.ok:
        # auto_reject → drift guard: keep last-known occurrences, flag for re-authoring.
        return _reauthor(engine, source_id, ev.error or "gates rejected the config")

    activate = ev.lane == "auto_accept" or (ev.lane == "review" and accept_review)
    if activate:
        with session_scope(engine, write=True) as s:
            n = repo.replace_source_occurrences(s, source_id, mosque_id, ev.rows)
            repo.set_source_state(
                s, source_id, triage_status="authored", confidence=ev.confidence,
                last_status="ok", last_fetched_at=now, source_html_hash=ev.html_hash,
                flags=ev.flags, authored_by=authored_by, authored_at=authored_at,
            )
            repo.record_extractor_run(s, source_id, ok=True, rows_written=n)
        return ExtractOutcome(source_id, True, n, ev.lane, "authored")

    # review, not accepted → withhold for review (not served).
    reason = "; ".join(ev.gate.reasons) if ev.gate else "review"
    with session_scope(engine, write=True) as s:
        repo.set_source_state(
            s, source_id, triage_status="review", confidence=ev.confidence,
            review_reason=reason, last_status="review", last_fetched_at=now,
            authored_by=authored_by, authored_at=authored_at,
        )
        repo.record_extractor_run(s, source_id, ok=True, rows_written=0)
    return ExtractOutcome(source_id, True, 0, ev.lane, "review")


def extract_source(
    engine,
    source_id: str,
    *,
    today: date | None = None,
    horizon_days: int = 60,
    fetcher=fetch,
    renderer=None,
    nav_renderer=None,
    accept_review: bool = False,
) -> ExtractOutcome:
    today = today or date.today()

    with session_scope(engine) as s:
        src = repo.get_source(s, source_id)
        if src is None:
            return ExtractOutcome(source_id, False, 0, "auto_reject", "missing", "no such source")
        url, config_raw, mosque_id = src.url, src.config, src.mosque_id
        requires_js = bool(src.requires_js)

    # Parse before fetching: the pager needs the config, and a bad config is a
    # re-author either way — no point spending a fetch on it.
    try:
        config = SourceConfig.from_json(config_raw)
    except ValueError as exc:
        return _reauthor(engine, source_id, f"config parse: {exc}")

    ev = evaluate_config(
        config, url, today=today, horizon_days=horizon_days, requires_js=requires_js,
        fetcher=fetcher, renderer=renderer, nav_renderer=nav_renderer,
    )
    return persist_evaluation(
        engine, source_id, mosque_id, config, ev, accept_review=accept_review
    )


def run_extract(
    engine,
    *,
    today: date | None = None,
    horizon_days: int = 60,
    fetcher=fetch,
    renderer=None,
    nav_renderer=None,
    concurrency: int = 16,
) -> list[ExtractOutcome]:
    with session_scope(engine) as s:
        source_ids = [src.id for src in repo.authored_sources(s)]

    def _one(sid: str) -> ExtractOutcome:
        return extract_source(
            engine, sid, today=today, horizon_days=horizon_days,
            fetcher=fetcher, renderer=renderer, nav_renderer=nav_renderer,
        )

    # source_ids are id-ordered; pool.map preserves order → deterministic results.
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        return list(pool.map(_one, source_ids))
