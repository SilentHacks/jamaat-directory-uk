from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from directory import repository as repo
from directory.db import session_scope
from directory.ingest.extractors.config_schema import SourceConfig
from directory.ingest.extractors.engine import extract
from directory.ingest.fetch import fetch
from directory.ingest.gates import run_gates
from directory.ingest.materialize import materialize


@dataclass
class ExtractOutcome:
    source_id: str
    ok: bool
    rows_written: int
    lane: str
    triage_status: str
    error: str | None = None


def _reauthor(engine, source_id, error) -> ExtractOutcome:
    with session_scope(engine) as s:
        repo.set_source_state(
            s, source_id, triage_status="needs_reauthor", last_status="error", last_error=error
        )
        repo.record_extractor_run(s, source_id, ok=False, rows_written=0, error=error)
    return ExtractOutcome(source_id, False, 0, "auto_reject", "needs_reauthor", error)


def extract_source(
    engine,
    source_id: str,
    *,
    today: date | None = None,
    horizon_days: int = 60,
    fetcher=fetch,
    renderer=None,
    accept_review: bool = False,
) -> ExtractOutcome:
    today = today or date.today()
    horizon_end = today + timedelta(days=horizon_days)

    with session_scope(engine) as s:
        src = repo.get_source(s, source_id)
        if src is None:
            return ExtractOutcome(source_id, False, 0, "auto_reject", "missing", "no such source")
        url, config_raw, mosque_id = src.url, src.config, src.mosque_id
        requires_js = bool(src.requires_js)

    fetched = fetcher(url, requires_js=requires_js, renderer=renderer)
    if fetched.error or not fetched.html:
        return _reauthor(engine, source_id, fetched.error or "empty body")

    try:
        config = SourceConfig.from_json(config_raw)
    except ValueError as exc:
        return _reauthor(engine, source_id, f"config parse: {exc}")

    result = extract(fetched.html, config, year=today.year, month=today.month)
    rows = materialize(result, config, horizon_start=today, horizon_end=horizon_end)
    gate = run_gates(config, result, rows, html_text=fetched.html)
    now = datetime.now(tz=UTC).isoformat(timespec="seconds")

    activate = gate.lane == "auto_accept" or (gate.lane == "review" and accept_review)
    if activate:
        with session_scope(engine) as s:
            n = repo.replace_source_occurrences(s, source_id, mosque_id, rows)
            repo.set_source_state(
                s, source_id, triage_status="authored", confidence=gate.confidence,
                last_status="ok", last_fetched_at=now, source_html_hash=fetched.html_hash,
            )
            repo.record_extractor_run(s, source_id, ok=True, rows_written=n)
        return ExtractOutcome(source_id, True, n, gate.lane, "authored")

    if gate.lane == "review":
        reason = "; ".join(gate.reasons)
        with session_scope(engine) as s:
            repo.set_source_state(
                s, source_id, triage_status="review", confidence=gate.confidence,
                review_reason=reason, last_status="review", last_fetched_at=now,
            )
            repo.record_extractor_run(s, source_id, ok=True, rows_written=0)
        return ExtractOutcome(source_id, True, 0, gate.lane, "review")

    # auto_reject → drift guard: keep last-known occurrences, flag for re-authoring.
    return _reauthor(engine, source_id, "; ".join(gate.reasons))


def run_extract(
    engine,
    *,
    today: date | None = None,
    horizon_days: int = 60,
    fetcher=fetch,
    renderer=None,
) -> list[ExtractOutcome]:
    with session_scope(engine) as s:
        source_ids = [src.id for src in repo.authored_sources(s)]
    return [
        extract_source(
            engine, sid, today=today, horizon_days=horizon_days, fetcher=fetcher, renderer=renderer
        )
        for sid in source_ids
    ]
