from enum import StrEnum


class Prayer(StrEnum):
    FAJR = "fajr"
    DHUHR = "dhuhr"
    ASR = "asr"
    MAGHRIB = "maghrib"
    ISHA = "isha"
    JUMUAH = "jumuah"

    @classmethod
    def parse(cls, value: str) -> "Prayer":
        try:
            return cls(value.strip().lower())
        except ValueError as exc:
            raise ValueError(f"unknown prayer: {value!r}") from exc


DAILY_PRAYERS: tuple[Prayer, ...] = (
    Prayer.FAJR,
    Prayer.DHUHR,
    Prayer.ASR,
    Prayer.MAGHRIB,
    Prayer.ISHA,
)
