from dataclasses import dataclass
from datetime import date

from directory.domain import Prayer
from directory.ingest.extractors.engine import Cell


@dataclass(frozen=True)
class OccurrenceRow:
    date: str  # ISO "YYYY-MM-DD"
    prayer: str  # Prayer enum value
    session_idx: int
    jamaah_time: str  # "HH:MM"
    begin_time: str | None
    label: str | None


def materialize_grid(
    cells: list[Cell], *, horizon_start: date, horizon_end: date
) -> list[OccurrenceRow]:
    merged: dict[tuple[date, Prayer], dict[str, str]] = {}
    for c in cells:
        if not (horizon_start <= c.date <= horizon_end):
            continue
        merged.setdefault((c.date, c.prayer), {})[c.kind] = c.time

    rows: list[OccurrenceRow] = []
    for (d, prayer), kinds in merged.items():
        jamaah = kinds.get("jamaah")
        if jamaah is None:
            continue
        rows.append(
            OccurrenceRow(
                date=d.isoformat(),
                prayer=prayer.value,
                session_idx=0,
                jamaah_time=jamaah,
                begin_time=kinds.get("begin"),
                label=None,
            )
        )
    rows.sort(key=lambda r: (r.date, r.prayer))
    return rows
