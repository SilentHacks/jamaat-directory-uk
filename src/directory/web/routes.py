import json
from importlib import resources

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import Engine

from directory import repository as repo
from directory.api.deps import get_engine
from directory.config import get_settings
from directory.db import session_scope

_templates_dir = resources.files("directory.web").joinpath("templates")
templates = Jinja2Templates(directory=str(_templates_dir))

router = APIRouter()


def _search(session, q: str | None):
    mosques = repo.iter_all_mosques(session)
    if q:
        ql = q.lower()
        mosques = [m for m in mosques if ql in m.name.lower() or (m.city and ql in m.city.lower())]
    return mosques


@router.get("/", response_class=HTMLResponse)
def index(request: Request, engine: Engine = Depends(get_engine)):  # noqa: B008
    with session_scope(engine) as s:
        mosques = repo.iter_all_mosques(s)
        return templates.TemplateResponse(
            request, "index.html", {"mosques": mosques, "count": len(mosques)}
        )


@router.get("/search", response_class=HTMLResponse)
def search(request: Request, q: str | None = None, engine: Engine = Depends(get_engine)):  # noqa: B008
    with session_scope(engine) as s:
        mosques = _search(s, q)
        return templates.TemplateResponse(request, "_mosque_rows.html", {"mosques": mosques})


@router.get("/mosque/{mosque_id}", response_class=HTMLResponse)
def mosque_detail(request: Request, mosque_id: str, engine: Engine = Depends(get_engine)):  # noqa: B008
    with session_scope(engine) as s:
        m = repo.get_mosque(s, mosque_id)
        if m is None:
            raise HTTPException(404, "mosque not found")
        has_times = repo.mosque_has_times(s, m.id)
        return templates.TemplateResponse(
            request, "mosque.html", {"m": m, "has_times": has_times}
        )


def require_web_admin(
    x_api_key: str | None = Header(None), key: str | None = None  # noqa: B008
) -> None:
    configured = get_settings().admin_api_key
    if configured is None:
        raise HTTPException(503, "admin API not configured")
    if x_api_key != configured and key != configured:
        raise HTTPException(401, "invalid API key")


def _config_pretty(raw: str | None) -> str:
    if not raw:
        return ""
    try:
        return json.dumps(json.loads(raw), indent=2)
    except ValueError:
        return raw


@router.get("/admin/review", response_class=HTMLResponse, dependencies=[Depends(require_web_admin)])  # noqa: B008
def review_list(
    request: Request, key: str | None = None, engine: Engine = Depends(get_engine)  # noqa: B008
):
    with session_scope(engine) as s:
        items = []
        for src in repo.sources_in_review(s):
            m = repo.get_mosque(s, src.mosque_id)
            items.append({"source_id": src.id, "name": m.name if m else None,
                          "reason": src.review_reason})
        return templates.TemplateResponse(
            request, "review_list.html", {"items": items, "key": key or ""}
        )


@router.get(
    "/admin/review/{source_id}", response_class=HTMLResponse,
    dependencies=[Depends(require_web_admin)],
)  # noqa: B008
def review_detail(
    request: Request, source_id: str, key: str | None = None,
    engine: Engine = Depends(get_engine),  # noqa: B008
):
    with session_scope(engine) as s:
        src = repo.get_source(s, source_id)
        if src is None:
            raise HTTPException(404, "source not found")
        m = repo.get_mosque(s, src.mosque_id)
        return templates.TemplateResponse(
            request, "review_detail.html",
            {"source": src, "name": m.name if m else None,
             "config_pretty": _config_pretty(src.config), "key": key or ""},
        )
