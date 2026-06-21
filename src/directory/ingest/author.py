# src/directory/ingest/author.py
import json
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from directory import repository as repo
from directory.db import session_scope
from directory.ingest.bespoke_store import load_bespoke, save_module
from directory.ingest.candidate_store import load_bundle
from directory.ingest.extractors.config_schema import SourceConfig
from directory.ingest.fetch import fetch
from directory.ingest.harness import AuthorHarness, get_harness
from directory.ingest.prompt import build_author_prompt, build_browse_prompt
from directory.ingest.runner import extract_source
from directory.models import Mosque


@dataclass
class AuthorOutcome:
    mosque_id: str
    outcome: str  # authored|review|needs_reauthor|no_candidate|skipped|failed
    model: str | None = None
    detail: str | None = None


def extract_json(text: str) -> str | None:
    """Return the first balanced top-level JSON object in ``text``, or None.

    Brace-counts while skipping anything inside double-quoted strings, so prose,
    code fences, and string values containing braces do not confuse it.
    """
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


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
    engine,
    mosque_id: str,
    res,
    default_url: str,
    harness_name: str,
    model: str,
    *,
    bespoke_root: Path | None,
    today: date | None,
    horizon_days: int,
    fetcher,
    renderer,
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
        if bespoke_root is None:
            return None, "bespoke shape only allowed from the agentic fallback"
        if not module_code:
            return None, "bespoke config without module_code"
        save_module(config.bespoke.module, module_code, root=bespoke_root)
        load_bespoke(bespoke_root)

    now = datetime.now(tz=UTC).isoformat(timespec="seconds")
    with session_scope(engine) as s:
        repo.create_or_update_source(
            s, source_id=mosque_id, mosque_id=mosque_id, url=chosen_url,
            platform=None, shape=config.shape, config=config.to_json(),
            requires_js=False, triage_status="authored",
        )
        repo.set_source_state(
            s, mosque_id, authored_by=f"{harness_name}:{model}", authored_at=now
        )

    result = extract_source(
        engine, mosque_id, today=today, horizon_days=horizon_days,
        fetcher=fetcher, renderer=renderer,
    )
    if result.triage_status in {"authored", "review"}:
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
) -> AuthorOutcome:
    with session_scope(engine) as s:
        src = repo.get_source(s, mosque_id)
        status = src.triage_status if src else None
    if src is None or status != "candidate":
        return AuthorOutcome(mosque_id, "skipped", detail=f"status={status}")

    bundle = load_bundle(mosque_id, root=candidate_root)
    if bundle is None or not bundle.candidates:
        with session_scope(engine) as s:
            repo.set_source_state(
                s, mosque_id, triage_status="no_timetable", last_status="no_candidate"
            )
        return AuthorOutcome(mosque_id, "no_candidate")

    prompt = build_author_prompt(bundle)
    default_url = bundle.candidates[0].url
    detail: str | None = None

    for model in models:
        res = harness.run(prompt, model=model)
        outcome, detail = _attempt(
            engine, mosque_id, res, default_url, harness.name, model,
            bespoke_root=None, today=today, horizon_days=horizon_days,
            fetcher=fetcher, renderer=renderer,
        )
        if outcome is not None:
            return outcome

    if fallback is not None:
        res = fallback.run(build_browse_prompt(bundle), model=fallback_model)
        outcome, detail = _attempt(
            engine, mosque_id, res, default_url, fallback.name, fallback_model,
            bespoke_root=bespoke_root, today=today, horizon_days=horizon_days,
            fetcher=fetcher, renderer=renderer,
        )
        if outcome is not None:
            return outcome

    with session_scope(engine) as s:
        repo.set_source_state(
            s, mosque_id, triage_status="needs_reauthor", last_status="error", last_error=detail
        )
    return AuthorOutcome(mosque_id, "needs_reauthor", detail=detail)


def order_by_city_size(mosques: list[Mosque]) -> list[Mosque]:
    counts = Counter(m.city for m in mosques)
    return sorted(mosques, key=lambda m: (-counts[m.city], m.id))


def run_authoring(
    engine,
    *,
    harness: AuthorHarness | None = None,
    harness_name: str = "opencode",
    fallback: AuthorHarness | None = None,
    fallback_name: str | None = None,
    fallback_model: str = "agentic",
    bespoke_root: Path | None = None,
    page_budget: int = 8,
    token_budget: int = 200_000,
    candidate_root: Path,
    models: tuple[str, ...],
    max_calls: int = 50,
    priority=order_by_city_size,
    today: date | None = None,
    horizon_days: int = 60,
    fetcher=fetch,
    renderer=None,
) -> list[AuthorOutcome]:
    harness = harness or get_harness(harness_name)
    if fallback is None and fallback_name:
        fallback = get_harness(fallback_name, page_budget=page_budget, token_budget=token_budget)
    with session_scope(engine) as s:
        candidates = repo.candidate_sources(s)
        mosques = [repo.get_mosque(s, c.mosque_id) for c in candidates]
        mosques = [m for m in mosques if m is not None]
        ordered_ids = [m.id for m in priority(mosques)]

    outcomes: list[AuthorOutcome] = []
    spent = 0
    for mid in ordered_ids:
        if spent >= max_calls:
            break
        out = author_mosque(
            engine, mid, harness=harness, candidate_root=candidate_root, models=models,
            fallback=fallback, fallback_model=fallback_model, bespoke_root=bespoke_root,
            today=today, horizon_days=horizon_days, fetcher=fetcher, renderer=renderer,
        )
        outcomes.append(out)
        if out.outcome not in {"no_candidate", "skipped"}:
            spent += 1
    return outcomes
