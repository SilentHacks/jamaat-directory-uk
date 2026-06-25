from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Engine

from directory import repository as repo
from directory.api.deps import get_engine
from directory.db import session_scope
from directory.schemas import MosqueOut

router = APIRouter(prefix="/mosques", tags=["mosques"])


def _parse_bbox(bbox: str | None) -> tuple[float, float, float, float] | None:
    if bbox is None:
        return None
    try:
        parts = [float(x) for x in bbox.split(",")]
    except ValueError:
        raise HTTPException(422, "bbox must be 'min_lng,min_lat,max_lng,max_lat'") from None
    if len(parts) != 4:
        raise HTTPException(422, "bbox must be 'min_lng,min_lat,max_lng,max_lat'")
    return (parts[0], parts[1], parts[2], parts[3])


def _parse_near(near: str | None) -> tuple[float, float] | None:
    if near is None:
        return None
    try:
        parts = [float(x) for x in near.split(",")]
    except ValueError:
        raise HTTPException(422, "near must be 'lat,lng'") from None
    if len(parts) != 2:
        raise HTTPException(422, "near must be 'lat,lng'")
    return (parts[0], parts[1])


@router.get("", response_model=list[MosqueOut])
def list_mosques(
    city: str | None = None,
    bbox: str | None = None,
    near: str | None = None,
    radius_km: float | None = None,
    has_times: bool | None = None,
    limit: int = Query(200, le=1000),
    offset: int = 0,
    engine: Engine = Depends(get_engine),  # noqa: B008
) -> list[MosqueOut]:
    bbox_t = _parse_bbox(bbox)
    near_t = _parse_near(near)
    if near_t is not None and radius_km is None:
        raise HTTPException(422, "near requires radius_km")
    with session_scope(engine) as s:
        rows = repo.list_mosques(
            s, city=city, bbox=bbox_t, near=near_t, radius_km=radius_km,
            has_times=has_times, limit=limit, offset=offset,
        )
        ids = [m.id for m in rows]
        with_times = repo.mosques_with_times(s, ids)
        sources = repo.sources_for_mosques(s, ids)
        return [
            MosqueOut.from_model(m, m.id in with_times, sources.get(m.id))
            for m in rows
        ]


@router.get("/{mosque_id}", response_model=MosqueOut)
def get_mosque(mosque_id: str, engine: Engine = Depends(get_engine)) -> MosqueOut:  # noqa: B008
    with session_scope(engine) as s:
        m = repo.get_mosque(s, mosque_id)
        if m is None:
            raise HTTPException(404, "mosque not found")
        return MosqueOut.from_model(
            m, repo.mosque_has_times(s, m.id), repo.source_for_mosque(s, m.id)
        )
