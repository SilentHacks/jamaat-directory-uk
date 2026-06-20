import json
import math

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from directory.models import Mosque, Occurrence

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
