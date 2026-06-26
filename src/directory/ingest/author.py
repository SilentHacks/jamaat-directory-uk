# src/directory/ingest/author.py
import json
import threading
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from pathlib import Path

from directory import repository as repo
from directory.db import session_scope
from directory.ingest.authoring_candidates import ConfigCandidate
from directory.ingest.config_enumerator import best_verified_candidate, enumerate_candidates
from directory.ingest.decision import (
    TERMINAL_LAST_STATUS,
    TERMINAL_OUTCOMES,
    AuthorDecision,
    config_from_table_mapping,
    extract_json,
    parse_decision,
)
from directory.ingest.diagnosis import (
    CandidateDiagnosis,
    DiagnoseReport,
    PageDiagnosis,
    diagnose_candidate,
)
from directory.ingest.discover import CandidateBundle
from directory.ingest.evidence import PageEvidence
from directory.ingest.extractors.bespoke import load_bespoke, save_module
from directory.ingest.extractors.config_schema import SourceConfig, authoring_problems
from directory.ingest.extractors.engine import WIDGET_EXTRACTORS
from directory.ingest.failure import classify_failure, feedback_prompt_kind
from directory.ingest.fetch import fetch
from directory.ingest.harness import (
    AuthorHarness,
    is_shutting_down,
    request_shutdown,
    reset_shutdown,
)
from directory.ingest.prompt import (
    build_browse_prompt,
    build_feedback_prompt,
    build_prompt,
    route_prompt_kind,
)
from directory.ingest.runner import MEDIA_SHAPES, ExtractOutcome, extract_source
from directory.ingest.verify import persist_verified_candidate, verify_candidate
from directory.models import Mosque

# Re-exported for the CLI and tests that import these from author (their historical
# home); the parsing/building lives in decision.py and the dry-run inspection in
# diagnosis.py now, but the author module stays their public entry point.
__all__ = [
    "AuthorDecision",
    "AuthorOutcome",
    "Budget",
    "CandidateDiagnosis",
    "DiagnoseReport",
    "PageDiagnosis",
    "author_mosque",
    "categorize_outcome",
    "config_from_table_mapping",
    "diagnose_candidate",
    "extract_json",
    "order_by_city_size",
    "parse_decision",
    "route_prompt_kind",
    "run_authoring",
    "run_reauthor",
    "run_verify_retry",
    "summarize_outcomes",
    "tally_outcome",
]

# Progress callback for the long-running funnels: (completed_count, total,
# result). ``result`` is None when a worker did no chargeable work (e.g. the
# spend budget was exhausted and the item was never dispatched).
ProgressFn = Callable[[int, int, object], None]


@dataclass
class AuthorOutcome:
    mosque_id: str
    # authored|review|deferred_media|no_timetable|needs_reauthor|no_candidate|skipped
    # |no_model|failed
    outcome: str
    model: str | None = None
    detail: str | None = None
    # Terminal sub-status ("no_timetable"|"wrong_site") so reporting can split a
    # misrouted site from a genuine "publishes no timetable" (both share the
    # ``no_timetable`` outcome). None for non-terminal outcomes.
    last_status: str | None = None
    # Chargeable harness calls made for this mosque, split by stage so an operator
    # can see model spend vs deterministic wins. The deterministic recovery path
    # makes zero of both.
    model_calls: int = 0
    fallback_calls: int = 0


def categorize_outcome(o: AuthorOutcome) -> str:
    """The reporting bucket for an outcome: splits ``authored`` into deterministic vs
    model (by whether a model authored it) and ``no_timetable`` into wrong_site vs
    no_timetable (by terminal sub-status). Everything else maps to its raw outcome
    (``review``/``deferred_media``/``needs_reauthor``/``skipped``/``no_candidate``/
    ``no_model``)."""
    if o.outcome == "authored":
        return "deterministic_authored" if o.model is None else "model_authored"
    if o.outcome == "no_timetable":
        return "wrong_site" if o.last_status == "wrong_site" else "no_timetable"
    return o.outcome


# Aggregate-spend keys folded into the same Counter as the status buckets; callers
# that need the mosque count exclude these (they count calls, not mosques).
SPEND_KEYS = ("model_calls", "fallback_calls")


def tally_outcome(tally: Counter[str], o: AuthorOutcome) -> None:
    """Fold one outcome into operator-facing counters: its reporting bucket plus
    aggregate ``model_calls``/``fallback_calls`` spend. The single bucketing rule,
    shared by the live progress reporter and the end-of-run summary so the two can
    never diverge."""
    tally[categorize_outcome(o)] += 1
    if o.model_calls:
        tally["model_calls"] += o.model_calls
    if o.fallback_calls:
        tally["fallback_calls"] += o.fallback_calls


def summarize_outcomes(outcomes: list[AuthorOutcome]) -> Counter[str]:
    """Tally a batch of outcomes into operator-facing counters (bucket counts plus
    aggregate ``model_calls``/``fallback_calls``), so a batch run is diagnosable at a
    glance — deterministic wins vs model spend."""
    tally: Counter[str] = Counter()
    for o in outcomes:
        tally_outcome(tally, o)
    return tally


@dataclass
class _Stage:
    """One rung of the funnel: a harness, the models to try on it, whether a
    bespoke module may be authored from it, whether to feed back on rejection, and
    whether it is the agentic browsing stage (which uses the live-browse prompt)."""

    harness: AuthorHarness
    models: tuple[str, ...]
    allow_bespoke: bool
    feedback: bool
    browse: bool


@dataclass
class _Ctx:
    """Everything an attempt needs beyond the per-stage inputs, threaded once."""

    engine: object
    bespoke_root: Path | None
    today: date | None
    horizon_days: int
    fetcher: object
    renderer: object
    nav_renderer: object


def _config_from_decision(
    decision: AuthorDecision, evidence: list[PageEvidence], ctx: _Ctx, allow_bespoke: bool
) -> SourceConfig:
    """Materialize a SourceConfig from a non-terminal decision, handling the bespoke
    module side effect. Raises ValueError on anything unusable — the funnel turns
    that into an "invalid config" attempt that feeds the model an actionable reason,
    rather than letting a structurally-broken config reach the engine and silently
    extract zero rows (a "no occurrences" rejection the model cannot learn from)."""
    if decision.outcome == "table_mapping":
        config = config_from_table_mapping(decision, evidence)
    else:
        config = decision.config
        if config is None:
            raise ValueError("decision produced no config")
        if config.shape == "bespoke":
            if not allow_bespoke or ctx.bespoke_root is None:
                raise ValueError("bespoke shape only allowed from the agentic fallback")
            if not decision.module_code:
                raise ValueError("bespoke config without module_code")
            save_module(config.bespoke.module, decision.module_code, root=ctx.bespoke_root)
            load_bespoke(ctx.bespoke_root)

    if config.shape == "widget":
        # A widget platform with no registered extractor would raise deep in
        # extraction (uncaught) and abort the whole authoring batch. Reject it
        # here so it becomes an "invalid config" attempt (feedback / needs_reauthor).
        platform = config.widget.platform if config.widget else None
        if platform not in WIDGET_EXTRACTORS:
            raise ValueError(
                f"unsupported widget platform {platform!r}; "
                f"supported: {sorted(WIDGET_EXTRACTORS)}"
            )

    problems = authoring_problems(config)
    if problems:
        raise ValueError("; ".join(problems))
    return config


def _persist_terminal(ctx: _Ctx, mosque_id: str, decision: AuthorDecision, authored_by: str):
    now = datetime.now(tz=UTC).isoformat(timespec="seconds")
    with session_scope(ctx.engine, write=True) as s:
        repo.set_source_state(
            s, mosque_id, triage_status="no_timetable",
            last_status=TERMINAL_LAST_STATUS[decision.outcome],
            last_error=decision.reason or decision.outcome,
            authored_by=authored_by, authored_at=now,
        )


def _attempt(
    ctx: _Ctx,
    mosque_id: str,
    res,
    default_url: str,
    harness_name: str,
    model: str,
    *,
    evidence: list[PageEvidence],
    allow_bespoke: bool,
) -> tuple[AuthorOutcome | None, str | None]:
    """Verify one harness reply *in memory* and persist only if it passes. Returns
    (outcome, None) when it terminally authored/reviewed/deferred/classified, else
    (None, detail) to feed back / escalate. A rejected reply never writes the
    source config (the Phase 4 guarantee)."""
    if not res.ok:
        return None, res.error
    try:
        decision = parse_decision(res.text, default_url)
    except ValueError as exc:
        return None, f"invalid config: {exc}"

    # Terminal classification: nothing a stronger model can author — record and stop.
    if decision.outcome in TERMINAL_OUTCOMES:
        _persist_terminal(ctx, mosque_id, decision, f"{harness_name}:{model}")
        return (
            AuthorOutcome(
                mosque_id, "no_timetable", model, detail=decision.reason,
                last_status=TERMINAL_LAST_STATUS[decision.outcome],
            ),
            None,
        )

    # The model could not decide → let the next stage try.
    if decision.outcome == "unknown":
        return None, decision.reason or "model returned outcome 'unknown'"

    try:
        config = _config_from_decision(decision, evidence, ctx, allow_bespoke)
    except ValueError as exc:
        return None, f"invalid config: {exc}"

    authored_by = f"{harness_name}:{model}"
    cand = ConfigCandidate(
        url=decision.url or default_url, config=config,
        source=f"model:{decision.outcome}", reason=decision.reason or "",
        confidence=0.5,
    )
    attempt = verify_candidate(
        cand, today=ctx.today, horizon_days=ctx.horizon_days, fetcher=ctx.fetcher,
        renderer=ctx.renderer, nav_renderer=ctx.nav_renderer,
    )
    if attempt.ok:
        out = persist_verified_candidate(ctx.engine, mosque_id, attempt, authored_by=authored_by)
        return AuthorOutcome(mosque_id, out.triage_status, model), None

    # JS render retry: a correct static config yields 0 rows when the timetable is
    # JS-injected. Re-verify once with requires_js=True (no speculative DB write)
    # before flagging the source.
    if config.shape not in MEDIA_SHAPES and ctx.renderer is not None:
        js_attempt = verify_candidate(
            replace(cand, requires_js=True), today=ctx.today, horizon_days=ctx.horizon_days,
            fetcher=ctx.fetcher, renderer=ctx.renderer, nav_renderer=ctx.nav_renderer,
        )
        if js_attempt.ok:
            out = persist_verified_candidate(
                ctx.engine, mosque_id, js_attempt, authored_by=authored_by
            )
            return AuthorOutcome(mosque_id, out.triage_status, model), None

    return None, attempt.error or "; ".join(attempt.reasons) or "gates rejected the config"


# ── deterministic pre-model recovery (Phase 3) ────────────────────────────────


def _deterministic_recover(ctx: _Ctx, bundle: CandidateBundle):
    """Run the config enumerator over the bundle's structured evidence and verify in
    memory, returning the best verified attempt or None. Only fires for bundles that
    carry evidence (a fresh discovery bundle, or a stale one re-enriched); an old
    evidence-less bundle goes straight to the model, unchanged."""
    if not bundle.evidence:
        return None
    candidates = enumerate_candidates(bundle.evidence)
    if not candidates:
        return None
    return best_verified_candidate(
        candidates, today=ctx.today, horizon_days=ctx.horizon_days, fetcher=ctx.fetcher,
        renderer=ctx.renderer, nav_renderer=ctx.nav_renderer,
    )


# ── attempt history (Phase 6) ─────────────────────────────────────────────────


def _record(
    history: list[dict], *, model: str, kind: str, res, detail: str | None,
    outcome: AuthorOutcome | None,
) -> None:
    history.append(
        {
            "model": model,
            "prompt_kind": kind,
            "output": (res.text or "")[:2000],
            "ok": res.ok,
            "detail": detail,
            "failure_kind": classify_failure(detail).value if outcome is None else None,
            "outcome": outcome.outcome if outcome is not None else None,
        }
    )


def _flush_history(runs_root: Path | None, mosque_id: str, history: list[dict]) -> None:
    """Persist the per-mosque attempt history (model, prompt kind, output, verify
    result, failure kind) for diagnosis. Gitignored — it may contain fetched site
    content."""
    if runs_root is None or not history:
        return
    runs_root.mkdir(parents=True, exist_ok=True)
    path = runs_root / f"{mosque_id}.json"
    path.write_text(
        json.dumps({"mosque_id": mosque_id, "attempts": history}, ensure_ascii=False),
        encoding="utf-8",
    )


def author_mosque(
    engine,
    mosque_id: str,
    *,
    harness: AuthorHarness,
    candidate_root: Path,
    models: tuple[str, ...],
    fallback: AuthorHarness | None = None,
    fallback_model: str = "agentic",
    bespoke_root: Path | None = None,
    today: date | None = None,
    horizon_days: int = 60,
    fetcher=fetch,
    renderer=None,
    nav_renderer=None,
    allowed_statuses: frozenset[str] = frozenset({"candidate"}),
    feedback_retries: int = 1,
    runs_root: Path | None = None,
    no_model: bool = False,
) -> AuthorOutcome:
    with session_scope(engine) as s:
        src = repo.get_source(s, mosque_id)
        status = src.triage_status if src else None
    if src is None or status not in allowed_statuses:
        return AuthorOutcome(mosque_id, "skipped", detail=f"status={status}")

    bundle = CandidateBundle.load(mosque_id, candidate_root)
    if bundle is None or not bundle.candidates:
        with session_scope(engine, write=True) as s:
            repo.set_source_state(
                s, mosque_id, triage_status="no_timetable", last_status="no_candidate"
            )
        return AuthorOutcome(mosque_id, "no_candidate")

    ctx = _Ctx(engine, bespoke_root, today, horizon_days, fetcher, renderer, nav_renderer)
    default_url = bundle.candidates[0].url
    evidence = bundle.evidence

    # Phase 3: deterministic recovery before any (paid) model call.
    recovered = _deterministic_recover(ctx, bundle)
    if recovered is not None:
        out = persist_verified_candidate(engine, mosque_id, recovered, authored_by="enumerator")
        return AuthorOutcome(mosque_id, out.triage_status, model=None,
                             detail=recovered.candidate.reason)

    # Deterministic-only pass (--no-model): evidence + enumerator already ran above
    # and could not recover. Leave the source a `candidate` untouched (no model call,
    # no needs_reauthor) so a later paid run can still pick it up — this is the lane
    # for measuring deterministic recovery before paying for any model.
    if no_model:
        return AuthorOutcome(
            mosque_id, "no_model", detail="no deterministic config (model disabled)"
        )

    stages = [_Stage(harness, models, allow_bespoke=False, feedback=True, browse=False)]
    if fallback is not None:
        stages.append(
            _Stage(fallback, (fallback_model,), allow_bespoke=True, feedback=False, browse=True)
        )

    initial_kind = route_prompt_kind(evidence)  # PromptKind.LEGACY when evidence is empty
    history: list[dict] = []
    detail: str | None = None
    model_calls = 0
    fallback_calls = 0

    def _stamp(out: AuthorOutcome) -> AuthorOutcome:
        out.model_calls = model_calls
        out.fallback_calls = fallback_calls
        return out

    for stage in stages:
        for model in stage.models:
            kind = initial_kind
            failed: list[tuple[str, str]] = []
            prev_reply = ""
            # One corrective re-prompt per model on the feedback stage; the prompt
            # kind is re-selected from the failure each time (Phase 6).
            attempts = 1 + (feedback_retries if stage.feedback else 0)
            for i in range(attempts):
                # A shutdown (operator Ctrl-C) killed in-flight agents; bail out
                # without writing a terminal status so this source stays a
                # 'candidate' and is picked up cleanly on the next run.
                if is_shutting_down():
                    raise KeyboardInterrupt
                if stage.browse:
                    prompt = build_browse_prompt(bundle)
                else:
                    base = build_prompt(kind, bundle, evidence, failed)
                    prompt = (
                        base if i == 0
                        else build_feedback_prompt(base, prev_reply, detail or "")
                    )
                res = stage.harness.run(prompt, model=model)
                if stage.browse:
                    fallback_calls += 1
                else:
                    model_calls += 1
                if is_shutting_down():
                    raise KeyboardInterrupt
                outcome, detail = _attempt(
                    ctx, mosque_id, res, default_url, stage.harness.name, model,
                    evidence=evidence, allow_bespoke=stage.allow_bespoke,
                )
                _record(history, model=model, kind=kind, res=res, detail=detail, outcome=outcome)
                if outcome is not None:
                    _flush_history(runs_root, mosque_id, history)
                    return _stamp(outcome)
                # A subprocess failure (timeout/crash) won't be fixed by feedback.
                if not res.ok:
                    break
                prev_reply = res.text
                failed.append((f"model:{kind}", detail or "rejected"))
                if evidence:
                    kind = feedback_prompt_kind(classify_failure(detail), kind)

    _flush_history(runs_root, mosque_id, history)
    with session_scope(engine, write=True) as s:
        repo.set_source_state(
            s, mosque_id, triage_status="needs_reauthor", last_status="error", last_error=detail
        )
    return _stamp(AuthorOutcome(mosque_id, "needs_reauthor", detail=detail))


def run_verify_retry(
    engine,
    *,
    today: date | None = None,
    horizon_days: int = 60,
    fetcher=fetch,
    renderer=None,
    nav_renderer=None,
    concurrency: int = 16,
    on_outcome: ProgressFn | None = None,
) -> list[ExtractOutcome]:
    """Free recovery pass: re-run extraction on every ``needs_reauthor`` source
    that still holds a config, with NO model call. Salvages render-flakiness
    false-negatives — a config that is correct but failed a flaky/transient fetch
    is promoted to authored/review/deferred_media; one that fails again simply
    stays needs_reauthor with its config retained. Run this before spending any
    paid model call on the re-author cohort."""
    with session_scope(engine) as s:
        source_ids = [src.id for src in repo.reauthor_sources(s)]

    def _one(sid: str) -> ExtractOutcome:
        return extract_source(
            engine, sid, today=today, horizon_days=horizon_days,
            fetcher=fetcher, renderer=renderer, nav_renderer=nav_renderer,
        )

    # source_ids are id-ordered; _drain_pool preserves submission order → results
    # stay deterministic regardless of completion order.
    results = _drain_pool(source_ids, _one, concurrency=concurrency, on_each=on_outcome)
    return [o for o in results if o is not None]


def order_by_city_size(mosques: list[Mosque]) -> list[Mosque]:
    counts = Counter(m.city for m in mosques)
    return sorted(mosques, key=lambda m: (-counts[m.city], m.id))


class Budget:
    """Thread-safe spend cap. Workers reserve a slot before a chargeable harness
    call and refund it when the attempt turns out to have made no chargeable call
    (skipped/no_candidate, the --no-model lane, or a deterministic recovery), so
    the ``max_calls`` cap counts model spend — not free wins — under concurrency."""

    def __init__(self, max_calls: int) -> None:
        self._max = max_calls
        self._spent = 0
        self._lock = threading.Lock()

    def try_reserve(self) -> bool:
        with self._lock:
            if self._spent >= self._max:
                return False
            self._spent += 1
            return True

    def refund(self) -> None:
        with self._lock:
            if self._spent > 0:
                self._spent -= 1

    @property
    def spent(self) -> int:
        with self._lock:
            return self._spent


def _drain_pool[T](
    items: list,
    worker: Callable[[object], T | None],
    *,
    concurrency: int,
    on_each: ProgressFn | None = None,
) -> list[T | None]:
    """Run ``worker`` over ``items`` across a thread pool, returning results in
    submission order (deterministic). ``on_each(done, total, result)`` is invoked
    on the main thread as each item completes, so callers get live progress
    instead of a single blocking batch.

    On ``KeyboardInterrupt`` (operator Ctrl-C): latch a shutdown so every live
    agent subprocess is terminated, stop dispatching queued work, and re-raise so
    the caller can print a summary. Workers already running observe the shutdown
    latch and abort without writing a terminal status."""
    total = len(items)
    results: list[T | None] = [None] * total
    if not items:
        return results

    reset_shutdown()
    stop = threading.Event()

    def _guarded(idx_item: tuple[int, object]) -> T | None:
        _, item = idx_item
        if stop.is_set() or is_shutting_down():
            return None
        return worker(item)

    pool = ThreadPoolExecutor(max_workers=max(1, concurrency))
    futures = {pool.submit(_guarded, (i, item)): i for i, item in enumerate(items)}
    try:
        done = 0
        for fut in as_completed(futures):
            results[futures[fut]] = fut.result()
            done += 1
            if on_each is not None:
                on_each(done, total, results[futures[fut]])
        pool.shutdown(wait=True)
    except KeyboardInterrupt:
        stop.set()
        request_shutdown()  # kill in-flight agent process groups
        pool.shutdown(wait=False, cancel_futures=True)
        raise
    return results


def run_authoring(
    engine,
    *,
    harness: AuthorHarness,
    candidate_root: Path,
    models: tuple[str, ...],
    fallback: AuthorHarness | None = None,
    fallback_model: str = "agentic",
    bespoke_root: Path | None = None,
    max_calls: int = 50,
    concurrency: int = 4,
    priority=order_by_city_size,
    today: date | None = None,
    horizon_days: int = 60,
    fetcher=fetch,
    renderer=None,
    nav_renderer=None,
    feedback_retries: int = 1,
    runs_root: Path | None = None,
    no_model: bool = False,
    on_outcome: ProgressFn | None = None,
) -> list[AuthorOutcome]:
    with session_scope(engine) as s:
        candidates = repo.candidate_sources(s)
        mosques = [repo.get_mosque(s, c.mosque_id) for c in candidates]
        mosques = [m for m in mosques if m is not None]
        ordered_ids = [m.id for m in priority(mosques)]

    budget = Budget(max_calls)

    def _worker(mid: str) -> AuthorOutcome | None:
        # Reserve before the (paid) harness call; budget-exhausted → don't dispatch.
        if not budget.try_reserve():
            return None
        # Snapshot the pre-author state so an operator Ctrl-C mid-attempt rolls the
        # source back to 'candidate' (even if the verify window had already written
        # a provisional 'authored'), leaving the run cleanly resumable.
        snap = _snapshot_source(engine, mid)
        try:
            out = author_mosque(
                engine, mid, harness=harness, candidate_root=candidate_root, models=models,
                fallback=fallback, fallback_model=fallback_model, bespoke_root=bespoke_root,
                today=today, horizon_days=horizon_days, fetcher=fetcher, renderer=renderer,
                nav_renderer=nav_renderer, feedback_retries=feedback_retries, runs_root=runs_root,
                no_model=no_model,
            )
        except KeyboardInterrupt:
            if snap is not None:
                _restore_source(engine, mid, snap)
            raise
        # Refund whenever no chargeable call was made — skipped/no_candidate, the
        # --no-model lane, AND a free deterministic recovery (which authors with
        # model_calls==0). The outcome name alone can't tell a deterministic
        # 'authored' from a paid one, so the budget must key off actual spend, not
        # the bucket — otherwise free wins silently burn the cap.
        if out.model_calls == 0 and out.fallback_calls == 0:
            budget.refund()
        return out

    results = _drain_pool(ordered_ids, _worker, concurrency=concurrency, on_each=on_outcome)
    return [o for o in results if o is not None]


@dataclass
class _SourceSnapshot:
    url: str | None
    platform: str | None
    shape: str | None
    config: str | None
    requires_js: bool
    triage_status: str | None


def _snapshot_source(engine, mosque_id: str) -> _SourceSnapshot | None:
    with session_scope(engine) as s:
        src = repo.get_source(s, mosque_id)
        if src is None:
            return None
        return _SourceSnapshot(
            src.url, src.platform, src.shape, src.config,
            bool(src.requires_js), src.triage_status,
        )


def _restore_source(engine, mosque_id: str, snap: _SourceSnapshot) -> None:
    """Put a source back exactly as it was before an authoring attempt — same
    config and the same triage status (so an interrupted ``candidate`` reverts to
    ``candidate`` and stays resumable, and a re-author rollback reverts to
    ``needs_reauthor``)."""
    with session_scope(engine, write=True) as s:
        repo.create_or_update_source(
            s, source_id=mosque_id, mosque_id=mosque_id, url=snap.url,
            platform=snap.platform, shape=snap.shape, config=snap.config,
            requires_js=snap.requires_js,
            triage_status=snap.triage_status or "needs_reauthor",
        )


def run_reauthor(
    engine,
    *,
    harness: AuthorHarness,
    candidate_root: Path,
    models: tuple[str, ...],
    fallback: AuthorHarness | None = None,
    fallback_model: str = "agentic",
    bespoke_root: Path | None = None,
    max_calls: int = 50,
    concurrency: int = 4,
    today: date | None = None,
    horizon_days: int = 60,
    fetcher=fetch,
    renderer=None,
    nav_renderer=None,
    feedback_retries: int = 1,
    runs_root: Path | None = None,
    on_outcome: ProgressFn | None = None,
) -> list[AuthorOutcome]:
    """Model re-authoring of the ``needs_reauthor`` cohort (the paid recovery path;
    run ``run_verify_retry`` first for the free salvage). Only sources that still
    have a candidate bundle on disk are eligible — a deterministic-discovery source
    has no bundle to prompt from and is left untouched. The prior config is
    snapshotted and restored if the attempt ends back in ``needs_reauthor`` *or* the
    model returns a terminal no_timetable verdict, so a non-deterministic model can
    never discard a config it failed to improve on."""
    with session_scope(engine) as s:
        ids = [src.id for src in repo.reauthor_sources(s)]
    ids = [mid for mid in ids if CandidateBundle.load(mid, candidate_root) is not None]

    budget = Budget(max_calls)

    def _worker(mid: str) -> AuthorOutcome | None:
        if not budget.try_reserve():
            return None
        snap = _snapshot_source(engine, mid)
        out = author_mosque(
            engine, mid, harness=harness, candidate_root=candidate_root, models=models,
            fallback=fallback, fallback_model=fallback_model, bespoke_root=bespoke_root,
            today=today, horizon_days=horizon_days, fetcher=fetcher, renderer=renderer,
            nav_renderer=nav_renderer, allowed_statuses=frozenset({"needs_reauthor"}),
            feedback_retries=feedback_retries, runs_root=runs_root,
        )
        if out.model_calls == 0 and out.fallback_calls == 0:
            budget.refund()  # no chargeable call (free deterministic win / skip)
        # The model failed to improve on what we had — put the prior config back so
        # a flaky-but-correct config survives a bad re-author roll. A terminal
        # no_timetable verdict during re-author is treated the same way: a retained
        # config that run_verify_retry could not salvage must not be shelved on a
        # single (possibly hallucinated) model classification.
        if out.outcome in {"needs_reauthor", "no_timetable"} and snap is not None:
            _restore_source(engine, mid, snap)
        return out

    results = _drain_pool(ids, _worker, concurrency=concurrency, on_each=on_outcome)
    return [o for o in results if o is not None]
