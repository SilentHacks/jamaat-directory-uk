from datetime import date

from directory import repository as repo
from directory.db import session_scope
from directory.ingest.extractors.config_schema import SourceConfig
from directory.ingest.fetch import fetch
from directory.ingest.runner import ExtractOutcome, extract_source


def approve_source(
    engine, source_id: str, *, today: date | None = None, horizon_days: int = 60,
    fetcher=fetch, renderer=None,
) -> ExtractOutcome:
    return extract_source(
        engine, source_id, today=today, horizon_days=horizon_days,
        fetcher=fetcher, renderer=renderer, accept_review=True,
    )


def reject_source(engine, source_id: str, *, reason: str | None = None) -> None:
    with session_scope(engine) as s:
        repo.set_source_state(
            s, source_id, triage_status="excluded",
            review_reason=reason or "rejected by reviewer", last_status="excluded",
        )


def fix_mapping(
    engine, source_id: str, config_json: str, *, today: date | None = None,
    horizon_days: int = 60, fetcher=fetch, renderer=None,
) -> ExtractOutcome:
    config = SourceConfig.from_json(config_json)  # raises ValueError on bad JSON/shape
    with session_scope(engine) as s:
        src = repo.get_source(s, source_id)
        if src is None:
            return ExtractOutcome(source_id, False, 0, "auto_reject", "missing", "no such source")
        repo.create_or_update_source(
            s, source_id=source_id, mosque_id=src.mosque_id, url=src.url,
            platform=src.platform, shape=config.shape, config=config.to_json(),
            requires_js=bool(src.requires_js), triage_status="review",
        )
    return extract_source(
        engine, source_id, today=today, horizon_days=horizon_days,
        fetcher=fetcher, renderer=renderer,
    )
