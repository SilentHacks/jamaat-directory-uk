from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import Engine, select

from directory.api.deps import get_engine
from directory.config import get_settings
from directory.db import session_scope
from directory.ingest.author import author_mosque
from directory.ingest.discover import discover_mosque
from directory.ingest.harness import get_harness
from directory.ingest.runner import extract_source
from directory.models import Source
from directory.repository import get_mosque, get_source, iter_all_mosques, sources_in_review

router = APIRouter(tags=["ops"])


def require_admin(x_api_key: str | None = Header(None)) -> None:  # noqa: B008
    key = get_settings().admin_api_key
    if key is None:
        raise HTTPException(503, "admin API not configured")
    if x_api_key != key:
        raise HTTPException(401, "invalid API key")


@router.get("/health")
def health(engine: Engine = Depends(get_engine)):  # noqa: B008
    with session_scope(engine) as s:
        return {"status": "ok", "mosques": len(iter_all_mosques(s))}


@router.post("/admin/sources/{source_id}/refresh", dependencies=[Depends(require_admin)])  # noqa: B008
def refresh_source(source_id: str, engine: Engine = Depends(get_engine)):  # noqa: B008
    with session_scope(engine) as s:
        if get_source(s, source_id) is None:
            raise HTTPException(404, "source not found")
    out = extract_source(engine, source_id)
    return {
        "source_id": out.source_id,
        "ok": out.ok,
        "lane": out.lane,
        "triage_status": out.triage_status,
        "rows_written": out.rows_written,
        "error": out.error,
    }


@router.post(
    "/admin/mosques/{mosque_id}/discover", dependencies=[Depends(require_admin)]
)  # noqa: B008
def discover_mosque_endpoint(mosque_id: str, engine: Engine = Depends(get_engine)):  # noqa: B008
    with session_scope(engine) as s:
        if get_mosque(s, mosque_id) is None:
            raise HTTPException(404, "mosque not found")
    out = discover_mosque(
        engine, mosque_id, candidate_root=get_settings().candidate_dir
    )
    return {
        "mosque_id": out.mosque_id,
        "outcome": out.outcome,
        "platform": out.platform,
        "detail": out.detail,
    }


@router.post(
    "/admin/mosques/{mosque_id}/author", dependencies=[Depends(require_admin)]
)  # noqa: B008
def author_mosque_endpoint(mosque_id: str, engine: Engine = Depends(get_engine)):  # noqa: B008
    settings = get_settings()
    with session_scope(engine) as s:
        if get_mosque(s, mosque_id) is None:
            raise HTTPException(404, "mosque not found")
    out = author_mosque(
        engine, mosque_id,
        harness=get_harness(settings.author_harness),
        candidate_root=settings.candidate_dir,
        models=(settings.author_model_cheap, settings.author_model_strong),
    )
    return {
        "mosque_id": out.mosque_id, "outcome": out.outcome,
        "model": out.model, "detail": out.detail,
    }


@router.get("/admin/review", dependencies=[Depends(require_admin)])  # noqa: B008
def review_queue(engine: Engine = Depends(get_engine)):  # noqa: B008
    with session_scope(engine) as s:
        rows = sources_in_review(s)
        out = []
        for r in rows:
            m = get_mosque(s, r.mosque_id)
            out.append({
                "source_id": r.id, "mosque_id": r.mosque_id,
                "name": m.name if m else None, "url": r.url,
                "reason": r.review_reason, "confidence": r.confidence,
            })
        return out


@router.get("/admin/sources", dependencies=[Depends(require_admin)])  # noqa: B008
def list_sources(engine: Engine = Depends(get_engine)):  # noqa: B008
    with session_scope(engine) as s:
        rows = s.scalars(select(Source).order_by(Source.id)).all()
        return [
            {
                "id": r.id,
                "mosque_id": r.mosque_id,
                "url": r.url,
                "platform": r.platform,
                "shape": r.shape,
                "triage_status": r.triage_status,
                "confidence": r.confidence,
                "last_status": r.last_status,
                "last_error": r.last_error,
            }
            for r in rows
        ]
