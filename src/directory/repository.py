import json
import math

from sqlalchemy import delete, exists, func, select
from sqlalchemy.orm import Session

from directory.models import ExtractorRun, Mosque, Occurrence, Source

_KM_PER_DEG_LAT = 111.0


def upsert_mosques(session: Session, mosques: list[dict]) -> int:
    count = 0
    for data in mosques:
        aliases = json.dumps(data.get("aliases", []))
        existing = session.get(Mosque, data["id"])
        if existing is None:
            session.add(
                Mosque(
                    id=data["id"],
                    name=data["name"],
                    aliases=aliases,
                    address=data.get("address"),
                    city=data.get("city"),
                    postcode=data.get("postcode"),
                    country=data.get("country", "GB"),
                    lat=data["lat"],
                    lng=data["lng"],
                    website_url=data.get("website_url"),
                    status=data.get("status", "active"),
                )
            )
        else:
            existing.name = data["name"]
            existing.aliases = aliases
            existing.address = data.get("address")
            existing.city = data.get("city")
            existing.postcode = data.get("postcode")
            existing.country = data.get("country", "GB")
            existing.lat = data["lat"]
            existing.lng = data["lng"]
            existing.website_url = data.get("website_url")
            existing.status = data.get("status", "active")
        count += 1
    return count


def mosque_has_times(session: Session, mosque_id: str) -> bool:
    return bool(
        session.scalar(
            select(exists().where(Occurrence.mosque_id == mosque_id))
        )
    )


def _within_radius(mlat: float, mlng: float, lat: float, lng: float, radius_km: float) -> bool:
    dlat = (mlat - lat) * _KM_PER_DEG_LAT
    dlng = (mlng - lng) * _KM_PER_DEG_LAT * math.cos(math.radians(lat))
    return math.hypot(dlat, dlng) <= radius_km


def list_mosques(
    session: Session,
    *,
    city: str | None = None,
    bbox: tuple[float, float, float, float] | None = None,
    near: tuple[float, float] | None = None,
    radius_km: float | None = None,
    has_times: bool | None = None,
    limit: int = 200,
    offset: int = 0,
) -> list[Mosque]:
    stmt = select(Mosque)
    if city is not None:
        stmt = stmt.where(Mosque.city == city)
    if bbox is not None:
        min_lng, min_lat, max_lng, max_lat = bbox
        stmt = stmt.where(
            Mosque.lng >= min_lng,
            Mosque.lng <= max_lng,
            Mosque.lat >= min_lat,
            Mosque.lat <= max_lat,
        )
    stmt = stmt.order_by(Mosque.id)
    rows = list(session.scalars(stmt))

    if near is not None:
        if radius_km is None:
            raise ValueError("near requires radius_km")
        lat, lng = near
        rows = [m for m in rows if _within_radius(m.lat, m.lng, lat, lng, radius_km)]

    if has_times is not None:
        rows = [m for m in rows if mosque_has_times(session, m.id) == has_times]

    return rows[offset : offset + limit]


def get_mosque(session: Session, mosque_id: str) -> Mosque | None:
    return session.get(Mosque, mosque_id)


def get_times(session: Session, mosque_id: str, date_from: str, date_to: str) -> list[Occurrence]:
    stmt = (
        select(Occurrence)
        .where(
            Occurrence.mosque_id == mosque_id,
            Occurrence.date >= date_from,
            Occurrence.date <= date_to,
        )
        .order_by(Occurrence.date, Occurrence.prayer, Occurrence.session_idx)
    )
    return list(session.scalars(stmt))


def query_times(
    session: Session,
    *,
    date: str,
    prayer: str | None = None,
    bbox: tuple[float, float, float, float] | None = None,
    near: tuple[float, float] | None = None,
    radius_km: float | None = None,
) -> list[tuple[Mosque, Occurrence]]:
    stmt = select(Mosque, Occurrence).join(Occurrence, Occurrence.mosque_id == Mosque.id)
    stmt = stmt.where(Occurrence.date == date)
    if prayer is not None:
        stmt = stmt.where(Occurrence.prayer == prayer)
    if bbox is not None:
        min_lng, min_lat, max_lng, max_lat = bbox
        stmt = stmt.where(
            Mosque.lng >= min_lng,
            Mosque.lng <= max_lng,
            Mosque.lat >= min_lat,
            Mosque.lat <= max_lat,
        )
    stmt = stmt.order_by(Mosque.id, Occurrence.prayer, Occurrence.session_idx)
    rows = [(m, o) for m, o in session.execute(stmt).all()]

    if near is not None:
        if radius_km is None:
            raise ValueError("near requires radius_km")
        lat, lng = near
        rows = [(m, o) for m, o in rows if _within_radius(m.lat, m.lng, lat, lng, radius_km)]

    return rows


def iter_all_mosques(session: Session) -> list[Mosque]:
    return list(session.scalars(select(Mosque).order_by(Mosque.id)))


def authored_sources(session: Session) -> list[Source]:
    stmt = (
        select(Source)
        .where(
            Source.triage_status.in_(("authored", "review", "needs_reauthor")),
            Source.url.is_not(None),
            Source.config.is_not(None),
        )
        .order_by(Source.id)
    )
    return list(session.scalars(stmt))


def get_source(session: Session, source_id: str) -> Source | None:
    return session.get(Source, source_id)


def replace_source_occurrences(
    session: Session, source_id: str, mosque_id: str, rows: list
) -> int:
    # Deletes by source_id then re-inserts. Assumes one source per mosque per
    # (date, prayer, session_idx); overlapping horizons from multiple sources
    # on the same mosque would collide on the Occurrence primary key.
    session.execute(delete(Occurrence).where(Occurrence.source_id == source_id))
    for r in rows:
        session.add(
            Occurrence(
                mosque_id=mosque_id,
                date=r.date,
                prayer=r.prayer,
                session_idx=r.session_idx,
                jamaah_time=r.jamaah_time,
                begin_time=r.begin_time,
                label=r.label,
                source_id=source_id,
            )
        )
    return len(rows)


def record_extractor_run(
    session: Session,
    source_id: str,
    *,
    ok: bool,
    rows_written: int,
    error: str | None = None,
) -> None:
    session.add(
        ExtractorRun(
            source_id=source_id,
            ok=1 if ok else 0,
            rows_written=rows_written,
            error=error,
        )
    )


def set_source_state(
    session: Session,
    source_id: str,
    *,
    triage_status: str | None = None,
    confidence: float | None = None,
    review_reason: str | None = None,
    last_status: str | None = None,
    last_error: str | None = None,
    last_fetched_at: str | None = None,
    source_html_hash: str | None = None,
    authored_by: str | None = None,
    authored_at: str | None = None,
    flags: list[str] | None = None,
) -> None:
    src = session.get(Source, source_id)
    if src is None:
        return
    if triage_status is not None:
        src.triage_status = triage_status
    if flags is not None:
        src.flags = json.dumps(flags)
    if confidence is not None:
        src.confidence = confidence
    if review_reason is not None:
        src.review_reason = review_reason
    if last_status is not None:
        src.last_status = last_status
    if last_error is not None:
        src.last_error = last_error
    if last_fetched_at is not None:
        src.last_fetched_at = last_fetched_at
    if source_html_hash is not None:
        src.source_html_hash = source_html_hash
    if authored_by is not None:
        src.authored_by = authored_by
    if authored_at is not None:
        src.authored_at = authored_at


def mosques_with_website(session: Session) -> list[Mosque]:
    return list(
        session.scalars(
            select(Mosque).where(Mosque.website_url.is_not(None)).order_by(Mosque.id)
        )
    )


def update_mosque_website(session: Session, mosque_id: str, website_url: str | None) -> None:
    m = session.get(Mosque, mosque_id)
    if m is None:
        return
    m.website_url = website_url
    m.updated_at = func.now()


def create_or_update_source(
    session: Session,
    *,
    source_id: str,
    mosque_id: str,
    url: str | None,
    platform: str | None,
    shape: str | None,
    config: str | None,
    requires_js: bool,
    triage_status: str,
) -> None:
    src = session.get(Source, source_id)
    if src is None:
        src = Source(id=source_id, mosque_id=mosque_id)
        session.add(src)
    src.mosque_id = mosque_id
    src.url = url
    src.platform = platform
    src.shape = shape
    src.config = config
    src.requires_js = 1 if requires_js else 0
    src.triage_status = triage_status


def candidate_sources(session: Session) -> list[Source]:
    return list(
        session.scalars(
            select(Source).where(Source.triage_status == "candidate").order_by(Source.id)
        )
    )


def sources_in_review(session: Session) -> list[Source]:
    return list(
        session.scalars(
            select(Source).where(Source.triage_status == "review").order_by(Source.id)
        )
    )


def source_for_mosque(session: Session, mosque_id: str) -> Source | None:
    return session.scalars(
        select(Source).where(Source.mosque_id == mosque_id).order_by(Source.id)
    ).first()


def sources_with_flag(session: Session, flag: str) -> list[Source]:
    rows = session.scalars(
        select(Source).where(Source.flags.is_not(None)).order_by(Source.id)
    )
    return [src for src in rows if flag in json.loads(src.flags or "[]")]


def mosques_for_discovery(session: Session) -> list[Mosque]:
    return list(
        session.scalars(
            select(Mosque).where(Mosque.website_url.is_not(None)).order_by(Mosque.id)
        )
    )
