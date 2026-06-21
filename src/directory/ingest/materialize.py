from dataclasses import dataclass
from datetime import date, timedelta

from directory.domain import Prayer
from directory.ingest.extractors.config_schema import (
    JumuahSpec,
    RulesSpec,
    SourceConfig,
)
from directory.ingest.extractors.engine import Cell, ExtractionResult
from directory.ingest.normalize import parse_time


@dataclass(frozen=True)
class OccurrenceRow:
    date: str  # ISO "YYYY-MM-DD"
    prayer: str  # Prayer enum value
    session_idx: int
    jamaah_time: str  # "HH:MM"
    begin_time: str | None
    label: str | None
    derived: bool = False  # jamaah_time computed from a begin + offset, not scraped


def materialize_grid(
    cells: list[Cell], *, horizon_start: date, horizon_end: date
) -> list[OccurrenceRow]:
    in_horizon = [c for c in cells if horizon_start <= c.date <= horizon_end]

    # Begin times, indexed for offset resolution (an offset may target another prayer).
    begins: dict[tuple[date, Prayer], str] = {
        (c.date, c.prayer): c.time
        for c in in_horizon
        if c.kind == "begin" and c.time is not None
    }

    merged: dict[tuple[date, Prayer], dict] = {}
    for c in in_horizon:
        slot = merged.setdefault((c.date, c.prayer), {})
        if c.kind == "begin" and c.time is not None:
            slot["begin"] = c.time
        elif c.kind == "jamaah":
            if c.time is not None:
                slot["jamaah"] = c.time
            elif c.offset_min is not None:
                slot["offset"] = (c.offset_min, c.base_prayer or c.prayer)

    rows: list[OccurrenceRow] = []
    for (d, prayer), slot in merged.items():
        derived = False
        jamaah = slot.get("jamaah")
        if jamaah is None and "offset" in slot:
            offset_min, base = slot["offset"]
            begin = begins.get((d, base))
            if begin is not None:
                jamaah = _apply_offset(begin, offset_min)
                derived = True
        if jamaah is None:
            continue
        rows.append(
            OccurrenceRow(
                date=d.isoformat(),
                prayer=prayer.value,
                session_idx=0,
                jamaah_time=jamaah,
                begin_time=slot.get("begin"),
                label=None,
                derived=derived,
            )
        )
    rows.sort(key=lambda r: (r.date, r.prayer))
    return rows


_SUMMER_MONTHS = {4, 5, 6, 7, 8, 9}


def _dates(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def _sessions_for(spec: JumuahSpec, day: date):
    if spec.seasonal:
        key = "summer" if day.month in _SUMMER_MONTHS else "winter"
        return spec.seasonal.get(key, spec.sessions)
    return spec.sessions


def materialize_jumuah(
    spec: JumuahSpec, *, horizon_start: date, horizon_end: date
) -> list[OccurrenceRow]:
    rows: list[OccurrenceRow] = []
    for day in _dates(horizon_start, horizon_end):
        if day.weekday() != 4:  # Friday
            continue
        for idx, sess in enumerate(_sessions_for(spec, day), start=1):
            t = parse_time(sess.time)
            if t is None:
                continue
            rows.append(
                OccurrenceRow(
                    date=day.isoformat(),
                    prayer="jumuah",
                    session_idx=idx,
                    jamaah_time=t,
                    begin_time=None,
                    label=sess.label,
                )
            )
    return rows


def _apply_offset(begin: str, minutes: int) -> str | None:
    hh, mm = (int(x) for x in begin.split(":"))
    total = hh * 60 + mm + minutes
    if not (0 <= total < 24 * 60):
        return None
    return f"{total // 60:02d}:{total % 60:02d}"


def materialize_rules(
    spec: RulesSpec,
    *,
    horizon_start: date,
    horizon_end: date,
    begin_lookup: dict[tuple[str, str], str] | None = None,
) -> list[OccurrenceRow]:
    rows: list[OccurrenceRow] = []
    for day in _dates(horizon_start, horizon_end):
        iso = day.isoformat()
        for rule in spec.rules:
            derived = False
            if rule.fixed:
                t = parse_time(rule.fixed)
            elif rule.offset_min is not None and begin_lookup is not None:
                begin = begin_lookup.get((iso, rule.prayer.value))
                t = _apply_offset(begin, rule.offset_min) if begin else None
                derived = t is not None
            else:
                t = None
            if t is None:
                continue
            rows.append(
                OccurrenceRow(
                    date=iso,
                    prayer=rule.prayer.value,
                    session_idx=0,
                    jamaah_time=t,
                    begin_time=None,
                    label=None,
                    derived=derived,
                )
            )
    return rows


def materialize(
    result: ExtractionResult,
    config: SourceConfig,
    *,
    horizon_start: date,
    horizon_end: date,
) -> list[OccurrenceRow]:
    rows = materialize_grid(result.cells, horizon_start=horizon_start, horizon_end=horizon_end)
    if config.shape == "rules" and config.rules:
        begin_lookup = {
            (r.date, r.prayer): r.begin_time for r in rows if r.begin_time
        }
        rows = rows + materialize_rules(
            config.rules,
            horizon_start=horizon_start,
            horizon_end=horizon_end,
            begin_lookup=begin_lookup,
        )
    if config.jumuah:
        rows = rows + materialize_jumuah(
            config.jumuah, horizon_start=horizon_start, horizon_end=horizon_end
        )
    return rows
