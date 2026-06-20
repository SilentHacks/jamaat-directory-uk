import hashlib
import json
from datetime import date as date_cls
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import Engine

from directory import repository as repo
from directory.api.deps import get_engine
from directory.api.mosques import _parse_bbox, _parse_near
from directory.config import get_settings
from directory.db import session_scope
from directory.schemas import MosqueOut, build_day_times

router = APIRouter(tags=["times"])


def _group_by_date(occurrences) -> list:
    by_date: dict[str, list] = {}
    for occ in occurrences:
        by_date.setdefault(occ.date, []).append(occ)
    return [build_day_times(d, by_date[d]) for d in sorted(by_date)]


@router.get("/mosques/{mosque_id}/times")
def mosque_times(
    mosque_id: str,
    date: str | None = None,
    from_: str | None = Query(None, alias="from"),
    to: str | None = None,
    engine: Engine = Depends(get_engine),  # noqa: B008
):
    if date is None and from_ is None and to is None:
        date = date_cls.today().isoformat()
    date_from = from_ or date
    date_to = to or date
    with session_scope(engine) as s:
        if repo.get_mosque(s, mosque_id) is None:
            raise HTTPException(404, "mosque not found")
        occ = repo.get_times(s, mosque_id, date_from, date_to)
        return _group_by_date(occ)


@router.get("/times")
def times(
    date: str,
    prayer: str | None = None,
    bbox: str | None = None,
    near: str | None = None,
    radius_km: float | None = None,
    engine: Engine = Depends(get_engine),  # noqa: B008
):
    bbox_t = _parse_bbox(bbox)
    near_t = _parse_near(near)
    if near_t is not None and radius_km is None:
        raise HTTPException(422, "near requires radius_km")
    with session_scope(engine) as s:
        rows = repo.query_times(
            s, date=date, prayer=prayer, bbox=bbox_t, near=near_t, radius_km=radius_km
        )
        return [
            {
                "mosque_id": m.id,
                "name": m.name,
                "lat": m.lat,
                "lng": m.lng,
                "prayer": o.prayer,
                "session_idx": o.session_idx,
                "label": o.label,
                "jamaah_time": o.jamaah_time,
                "begin_time": o.begin_time,
            }
            for m, o in rows
        ]


@router.get("/snapshot")
def snapshot(response: Response, engine: Engine = Depends(get_engine)):  # noqa: B008
    settings = get_settings()
    today = date_cls.today()
    horizon = (today + timedelta(days=settings.snapshot_horizon_days)).isoformat()
    with session_scope(engine) as s:
        mosques = repo.iter_all_mosques(s)
        payload_mosques = []
        for m in mosques:
            occ = repo.get_times(s, m.id, today.isoformat(), horizon)
            payload_mosques.append(
                {
                    "mosque": MosqueOut.from_model(m, bool(occ)).model_dump(),
                    "times": [dt.model_dump() for dt in _group_by_date(occ)],
                }
            )
    body = {
        "generated_at": today.isoformat(),
        "count": len(payload_mosques),
        "mosques": payload_mosques,
    }
    etag = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:16]
    response.headers["ETag"] = f'"{etag}"'
    return body
