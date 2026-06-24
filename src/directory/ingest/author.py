# src/directory/ingest/author.py
import json
import threading
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from directory import repository as repo
from directory.db import session_scope
from directory.ingest.discover import CandidateBundle
from directory.ingest.extractors.bespoke import load_bespoke, save_module
from directory.ingest.extractors.config_schema import SourceConfig
from directory.ingest.fetch import fetch
from directory.ingest.harness import AuthorHarness
from directory.ingest.jsonscan import first_json_object
from directory.ingest.prompt import build_author_prompt, build_browse_prompt
from directory.ingest.runner import ExtractOutcome, extract_source
from directory.models import Mosque


@dataclass
class AuthorOutcome:
    mosque_id: str
    outcome: str  # authored|review|deferred_media|needs_reauthor|no_candidate|skipped|failed
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


def _parse_output(raw: str, default_url: str) -> tuple[str, SourceConfig, str | None]:
    """Parse a harness reply into (chosen_url, SourceConfig, module_code).

    Accepts {"url":..., "config": {...}, "module_code": "..."} or a bare config.
    `module_code` is the bespoke module source, present only on the envelope form.
    Raises ValueError (incl. pydantic ValidationError) on anything invalid.
    """
    obj = extract_json(raw)
    if obj is None:
        raise ValueError("no JSON object in harness output")
    data = json.loads(obj)
    module_code: str | None = None
    if isinstance(data, dict) and "config" in data:
        cfg_obj = data["config"]
        url = data.get("url") or default_url
        module_code = data.get("module_code")
    else:
        cfg_obj = data
        url = default_url
    return url, SourceConfig.model_validate(cfg_obj), module_code


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
    authored/reviewed, else (None, detail) to escalate to the next stage."""
    if not res.ok:
        return None, res.error
    try:
        chosen_url, config, module_code = _parse_output(res.text, default_url)
    except ValueError as exc:
        return None, f"invalid config: {exc}"

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

    stages = [_Stage(harness, models, build_author_prompt, allow_bespoke=False)]
    if fallback is not None:
        stages.append(
            _Stage(fallback, (fallback_model,), build_browse_prompt, allow_bespoke=True)
        )

    ctx = _Ctx(engine, bespoke_root, today, horizon_days, fetcher, renderer, nav_renderer)
    default_url = bundle.candidates[0].url
    detail: str | None = None

    for stage in stages:
        prompt = stage.prompt(bundle)
        for model in stage.models:
            res = stage.harness.run(prompt, model=model)
            outcome, detail = _attempt(
                ctx, mosque_id, res, default_url, stage.harness.name, model,
                allow_bespoke=stage.allow_bespoke,
            )
            if outcome is not None:
                return outcome

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

    # source_ids are id-ordered; pool.map preserves order → deterministic results.
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        return list(pool.map(_one, source_ids))


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
        out = author_mosque(
            engine, mid, harness=harness, candidate_root=candidate_root, models=models,
            fallback=fallback, fallback_model=fallback_model, bespoke_root=bespoke_root,
            today=today, horizon_days=horizon_days, fetcher=fetcher, renderer=renderer,
            nav_renderer=nav_renderer,
        )
        if out.outcome in _FREE_OUTCOMES:
            budget.refund()
        return out

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        results = list(pool.map(_worker, ordered_ids))
    return [o for o in results if o is not None]


@dataclass
class _SourceSnapshot:
    url: str | None
    platform: str | None
    shape: str | None
    config: str | None
    requires_js: bool


def _snapshot_source(engine, mosque_id: str) -> _SourceSnapshot | None:
    with session_scope(engine) as s:
        src = repo.get_source(s, mosque_id)
        if src is None:
            return None
        return _SourceSnapshot(src.url, src.platform, src.shape, src.config, bool(src.requires_js))


def _restore_source(engine, mosque_id: str, snap: _SourceSnapshot) -> None:
    with session_scope(engine, write=True) as s:
        repo.create_or_update_source(
            s, source_id=mosque_id, mosque_id=mosque_id, url=snap.url,
            platform=snap.platform, shape=snap.shape, config=snap.config,
            requires_js=snap.requires_js, triage_status="needs_reauthor",
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
        )
        if out.outcome in _FREE_OUTCOMES:
            budget.refund()
        # The model failed to improve on what we had — put the prior config back so
        # a flaky-but-correct config survives a bad re-author roll.
        if out.outcome == "needs_reauthor" and snap is not None:
            _restore_source(engine, mid, snap)
        return out

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        results = list(pool.map(_worker, ids))
    return [o for o in results if o is not None]
