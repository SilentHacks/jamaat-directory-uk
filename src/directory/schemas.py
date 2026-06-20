from pydantic import BaseModel

from directory.domain import DAILY_PRAYERS, Prayer
from directory.models import Mosque, Occurrence


class JumuahSession(BaseModel):
    label: str | None
    time: str


class MosqueOut(BaseModel):
    id: str
    name: str
    aliases: list[str]
    address: str | None
    city: str | None
    postcode: str | None
    country: str
    lat: float
    lng: float
    website_url: str | None
    status: str
    has_times: bool

    @classmethod
    def from_model(cls, m: Mosque, has_times: bool) -> "MosqueOut":
        return cls(
            id=m.id,
            name=m.name,
            aliases=m.aliases_list,
            address=m.address,
            city=m.city,
            postcode=m.postcode,
            country=m.country or "GB",
            lat=m.lat,
            lng=m.lng,
            website_url=m.website_url,
            status=m.status,
            has_times=has_times,
        )


class DayTimes(BaseModel):
    date: str
    fajr: str | None = None
    dhuhr: str | None = None
    asr: str | None = None
    maghrib: str | None = None
    isha: str | None = None
    jumuah: list[JumuahSession] = []
    begin: dict[str, str] | None = None


def build_day_times(date: str, occurrences: list[Occurrence]) -> DayTimes:
    daily: dict[str, str] = {}
    begin: dict[str, str] = {}
    jumuah: list[JumuahSession] = []

    for occ in occurrences:
        if occ.prayer == Prayer.JUMUAH.value:
            jumuah.append(JumuahSession(label=occ.label, time=occ.jamaah_time))
        elif occ.prayer in {p.value for p in DAILY_PRAYERS}:
            daily[occ.prayer] = occ.jamaah_time
            if occ.begin_time:
                begin[occ.prayer] = occ.begin_time

    jumuah.sort(key=lambda s: s.time)
    return DayTimes(
        date=date,
        fajr=daily.get("fajr"),
        dhuhr=daily.get("dhuhr"),
        asr=daily.get("asr"),
        maghrib=daily.get("maghrib"),
        isha=daily.get("isha"),
        jumuah=jumuah,
        begin=begin or None,
    )
