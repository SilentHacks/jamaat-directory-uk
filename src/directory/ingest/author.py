# src/directory/ingest/author.py
import json
import threading
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from directory import repository as repo
from directory.db import session_scope
from directory.ingest.discover import CandidateBundle
from directory.ingest.extractors.bespoke import load_bespoke, save_module
from directory.ingest.extractors.config_schema import MediaSpec, SourceConfig
from directory.ingest.fetch import fetch
from directory.ingest.harness import (
    AuthorHarness,
    is_shutting_down,
    request_shutdown,
    reset_shutdown,
)
from directory.ingest.jsonscan import first_json_object
from directory.ingest.prompt import (
    build_author_prompt,
    build_browse_prompt,
    build_feedback_prompt,
)
from directory.ingest.runner import ExtractOutcome, extract_source
from directory.models import Mosque

# Progress callback for the long-running funnels: (completed_count, total,
# result). ``result`` is None when a worker did no chargeable work (e.g. the
# spend budget was exhausted and the item was never dispatched).
ProgressFn = Callable[[int, int, object], None]


@dataclass
class AuthorOutcome:
    mosque_id: str
    # authored|review|deferred_media|no_timetable|needs_reauthor|no_candidate|skipped|failed
    outcome: str
    model: str | None = None
    detail: str | None = None


@dataclass
class _Stage:
    """One rung of the §5.1 funnel: a harness, the models to try on it, the prompt
    builder, and whether a bespoke module may be authored from it."""

    harness: AuthorHarness
    models: tuple[str, ...]
    prompt: Callable[[CandidateBundle], str]
    allow_bespoke: bool
    feedback: bool  # re-prompt with the verify error on a rejected config


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


def extract_json(text: str) -> str | None:
    """Return the first balanced top-level JSON object in ``text``, or None."""
    return first_json_object(text)


# Model outcomes that terminate authoring with no extractable timetable; both land
# the source on triage_status="no_timetable" (the last_status detail distinguishes
# them). See gates/discovery for the deterministic counterparts.
_TERMINAL_OUTCOMES = frozenset({"no_timetable", "wrong_site"})
# wrong_site keeps its own last_status so a misrouted website is distinguishable
# from a genuine "this mosque publishes no timetable".
_TERMINAL_LAST_STATUS = {"no_timetable": "no_timetable", "wrong_site": "wrong_site"}


@dataclass
class AuthorDecision:
    """A parsed harness reply. ``outcome`` routes what happens next:
    - ``config``: ``config`` (+ optional ``module_code``) is verified and persisted.
    - ``media``: ``config`` is an image/pdf media config to defer.
    - ``no_timetable`` / ``wrong_site``: terminal — record and stop, no escalation.
    - ``unknown``: the model could not decide; escalate to the next stage.
    """

    outcome: str
    config: SourceConfig | None = None
    url: str | None = None
    module_code: str | None = None
    reason: str | None = None


def parse_decision(raw: str, default_url: str) -> AuthorDecision:
    """Parse a harness reply into an AuthorDecision.

    Accepts the historical config envelopes — ``{"url":..., "config": {...},
    "module_code": "..."}`` or a bare config object — and the narrow decision
    envelopes ``{"outcome": "media"|"no_timetable"|"wrong_site"|"unknown", ...}``.
    Raises ValueError (incl. pydantic ValidationError) on anything invalid.
    """
    obj = extract_json(raw)
    if obj is None:
        raise ValueError("no JSON object in harness output")
    data = json.loads(obj)
    if not isinstance(data, dict):
        raise ValueError("harness output is not a JSON object")

    outcome = data.get("outcome")

    if outcome in _TERMINAL_OUTCOMES:
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


def _attempt(
    ctx: _Ctx,
    mosque_id: str,
    res,
    default_url: str,
    harness_name: str,
    model: str,
    *,
    allow_bespoke: bool,
) -> tuple[AuthorOutcome | None, str | None]:
    """Verify one harness reply. Returns (outcome, None) when it terminally
    authored/reviewed/classified, else (None, detail) to escalate to the next
    stage."""
    if not res.ok:
        return None, res.error
    try:
        decision = parse_decision(res.text, default_url)
    except ValueError as exc:
        return None, f"invalid config: {exc}"

    # Terminal classification: the model judged the site has no extractable
    # timetable. Record it and stop — there is nothing a stronger model can author,
    # so do not escalate.
    if decision.outcome in _TERMINAL_OUTCOMES:
        now = datetime.now(tz=UTC).isoformat(timespec="seconds")
        with session_scope(ctx.engine, write=True) as s:
            repo.set_source_state(
                s, mosque_id, triage_status="no_timetable",
                last_status=_TERMINAL_LAST_STATUS[decision.outcome],
                last_error=decision.reason or decision.outcome,
                authored_by=f"{harness_name}:{model}", authored_at=now,
            )
        return AuthorOutcome(mosque_id, "no_timetable", model, detail=decision.reason), None

    # The model could not decide → let the next stage try.
    if decision.outcome == "unknown":
        return None, decision.reason or "model returned outcome 'unknown'"

    config = decision.config
    chosen_url = decision.url or default_url
    module_code = decision.module_code

    if config.shape == "bespoke":
        if not allow_bespoke or ctx.bespoke_root is None:
            return None, "bespoke shape only allowed from the agentic fallback"
        if not module_code:
            return None, "bespoke config without module_code"
        save_module(config.bespoke.module, module_code, root=ctx.bespoke_root)
        load_bespoke(ctx.bespoke_root)

    now = datetime.now(tz=UTC).isoformat(timespec="seconds")
    with session_scope(ctx.engine, write=True) as s:
        repo.create_or_update_source(
            s, source_id=mosque_id, mosque_id=mosque_id, url=chosen_url,
            platform=None, shape=config.shape, config=config.to_json(),
            requires_js=False, triage_status="authored",
        )
        repo.set_source_state(
            s, mosque_id, authored_by=f"{harness_name}:{model}", authored_at=now
        )

    result = extract_source(
        ctx.engine, mosque_id, today=ctx.today, horizon_days=ctx.horizon_days,
        fetcher=ctx.fetcher, renderer=ctx.renderer, nav_renderer=ctx.nav_renderer,
    )
    # deferred_media is terminal: the image/PDF was classified and recorded, so
    # there is nothing for a stronger model to improve — do not escalate.
    if result.triage_status in {"authored", "review", "deferred_media"}:
        return AuthorOutcome(mosque_id, result.triage_status, model), None

    # The config is stored requires_js=False, so the verify above fetched the page
    # statically. A JS-rendered timetable then yields 0 rows even though the config
    # is correct. Retry the verify once with rendering before flagging the source.
    if ctx.renderer is not None:
        with session_scope(ctx.engine, write=True) as s:
            repo.set_source_state(s, mosque_id, requires_js=True)
        result = extract_source(
            ctx.engine, mosque_id, today=ctx.today, horizon_days=ctx.horizon_days,
            fetcher=ctx.fetcher, renderer=ctx.renderer, nav_renderer=ctx.nav_renderer,
        )
        if result.triage_status in {"authored", "review", "deferred_media"}:
            return AuthorOutcome(mosque_id, result.triage_status, model), None

    return None, result.error or "gates rejected the authored config"


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

    stages = [_Stage(harness, models, build_author_prompt, allow_bespoke=False, feedback=True)]
    if fallback is not None:
        stages.append(
            _Stage(fallback, (fallback_model,), build_browse_prompt,
                   allow_bespoke=True, feedback=False)
        )

    ctx = _Ctx(engine, bespoke_root, today, horizon_days, fetcher, renderer, nav_renderer)
    default_url = bundle.candidates[0].url
    detail: str | None = None

    for stage in stages:
        base_prompt = stage.prompt(bundle)
        for model in stage.models:
            prompt = base_prompt
            # One corrective re-prompt per model on the feedback stage: a rejected
            # config (wrong selectors/indices, invalid shape) is re-fed its own
            # error so the tool-enabled agent can verify and fix it.
            attempts = 1 + (feedback_retries if stage.feedback else 0)
            for _ in range(attempts):
                # A shutdown (operator Ctrl-C) killed in-flight agents; bail out
                # without writing a terminal status so this source stays a
                # 'candidate' and is picked up cleanly on the next run.
                if is_shutting_down():
                    raise KeyboardInterrupt
                res = stage.harness.run(prompt, model=model)
                if is_shutting_down():
                    raise KeyboardInterrupt
                outcome, detail = _attempt(
                    ctx, mosque_id, res, default_url, stage.harness.name, model,
                    allow_bespoke=stage.allow_bespoke,
                )
                if outcome is not None:
                    return outcome
                # A subprocess failure (timeout/crash) won't be fixed by feedback.
                if not res.ok:
                    break
                prompt = build_feedback_prompt(base_prompt, res.text, detail or "")

    with session_scope(engine, write=True) as s:
        repo.set_source_state(
            s, mosque_id, triage_status="needs_reauthor", last_status="error", last_error=detail
        )
    return AuthorOutcome(mosque_id, "needs_reauthor", detail=detail)


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
    call and refund it when the attempt turns out free (skipped/no_candidate),
    so the ``max_calls`` cap holds under concurrency."""

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


_FREE_OUTCOMES = {"no_candidate", "skipped"}


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
                nav_renderer=nav_renderer, feedback_retries=feedback_retries,
            )
        except KeyboardInterrupt:
            if snap is not None:
                _restore_source(engine, mid, snap)
            raise
        if out.outcome in _FREE_OUTCOMES:
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
    on_outcome: ProgressFn | None = None,
) -> list[AuthorOutcome]:
    """Model re-authoring of the ``needs_reauthor`` cohort (the paid recovery path;
    run ``run_verify_retry`` first for the free salvage). Only sources that still
    have a candidate bundle on disk are eligible — a deterministic-discovery source
    has no bundle to prompt from and is left untouched. The prior config is
    snapshotted and restored if the attempt ends back in ``needs_reauthor``, so a
    non-deterministic model can never discard a config it failed to improve on."""
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
            feedback_retries=feedback_retries,
        )
        if out.outcome in _FREE_OUTCOMES:
            budget.refund()
        # The model failed to improve on what we had — put the prior config back so
        # a flaky-but-correct config survives a bad re-author roll.
        if out.outcome == "needs_reauthor" and snap is not None:
            _restore_source(engine, mid, snap)
        return out

    results = _drain_pool(ids, _worker, concurrency=concurrency, on_each=on_outcome)
    return [o for o in results if o is not None]
