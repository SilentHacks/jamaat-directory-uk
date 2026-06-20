from importlib import resources

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import Engine

from directory import repository as repo
from directory.api.deps import get_engine
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
