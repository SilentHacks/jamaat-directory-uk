from collections import defaultdict
from dataclasses import dataclass

from directory.domain import DAILY_PRAYERS
from directory.ingest.extractors.config_schema import SourceConfig
from directory.ingest.extractors.engine import ExtractionResult
from directory.ingest.materialize import OccurrenceRow

# Plausible jamaah windows in minutes-from-midnight, inclusive.
_WINDOWS: dict[str, tuple[int, int]] = {
    "fajr": (2 * 60, 7 * 60 + 30),
    "dhuhr": (11 * 60 + 30, 15 * 60),
    "asr": (13 * 60, 19 * 60 + 30),
    "maghrib": (15 * 60 + 30, 22 * 60 + 30),
    "isha": (17 * 60, 23 * 60 + 59),
    "jumuah": (12 * 60, 15 * 60),
}
_DAILY = [p.value for p in DAILY_PRAYERS]


@dataclass
class GateResult:
    lane: str  # "auto_accept" | "review" | "auto_reject"
    confidence: float
    reasons: list[str]


def _minutes(hhmm: str) -> int:
    hh, mm = (int(x) for x in hhmm.split(":"))
    return hh * 60 + mm


def lint_config(config: SourceConfig) -> list[str]:
    problems: list[str] = []
    grid = config.grid
    if config.shape in {"html_table", "html_repeated"}:
        if grid is None or not grid.columns:
            problems.append("grid shape has no columns")
        else:
            for col in grid.columns:
                if col.kind == "jamaah" and col.prayer is None:
                    problems.append(f"jamaah column without prayer: {col!r}")
    return problems


def _has_jumuah(occ: list[OccurrenceRow]) -> bool:
    return any(o.prayer == "jumuah" for o in occ)


def run_gates(
    config: SourceConfig,
    result: ExtractionResult,
    occurrences: list[OccurrenceRow],
    *,
    html_text: str = "",
) -> GateResult:
    reasons: list[str] = []

    lint = lint_config(config)
    if lint:
        return GateResult("auto_reject", 0.0, [f"lint: {p}" for p in lint])

    daily = [o for o in occurrences if o.prayer in _DAILY]
    if not daily and not _has_jumuah(occurrences):
        return GateResult("auto_reject", 0.0, ["no occurrences produced"])

    by_date: dict[str, dict[str, OccurrenceRow]] = defaultdict(dict)
    for o in daily:
        by_date[o.date][o.prayer] = o

    has_begin = any(o.begin_time for o in daily)

    for d, prayers in by_date.items():
        missing = [p for p in _DAILY if p not in prayers]
        if missing:
            return GateResult("auto_reject", 0.0, [f"{d}: missing {missing}"])
        mins = [_minutes(prayers[p].jamaah_time) for p in _DAILY]
        if mins != sorted(mins):
            return GateResult("auto_reject", 0.0, [f"{d}: non-monotonic day"])
        for p in _DAILY:
            lo, hi = _WINDOWS[p]
            if not (lo <= _minutes(prayers[p].jamaah_time) <= hi):
                return GateResult("auto_reject", 0.0, [f"{d}: {p} out of window"])

    # Self-extraction match: every distinct jamaah time must appear in the source.
    if html_text:
        for o in occurrences:
            if o.jamaah_time not in html_text:
                return GateResult("auto_reject", 0.0, [f"self-match failed for {o.jamaah_time}"])

    # Jumu'ah gates.
    jum_by_date: dict[str, list[OccurrenceRow]] = defaultdict(list)
    for o in occurrences:
        if o.prayer == "jumuah":
            jum_by_date[o.date].append(o)
    for d, sessions in jum_by_date.items():
        if not (1 <= len(sessions) <= 4):
            return GateResult("auto_reject", 0.0, [f"{d}: bad jumuah session count"])
        times = [_minutes(s.jamaah_time) for s in sorted(sessions, key=lambda s: s.session_idx)]
        if times != sorted(times) or len(set(times)) != len(times):
            return GateResult("auto_reject", 0.0, [f"{d}: jumuah sessions not ordered/distinct"])
        lo, hi = _WINDOWS["jumuah"]
        if any(not (lo <= t <= hi) for t in times):
            return GateResult("auto_reject", 0.0, [f"{d}: jumuah out of window"])

    # Constant-column red flag → review (fixed iqamah is common, so soften to review).
    distinct_dates = len(by_date)
    if distinct_dates >= 7 and not has_begin:
        constant = all(
            len({by_date[d][p].jamaah_time for d in by_date}) == 1 for p in _DAILY
        )
        if constant:
            reasons.append("all daily prayers constant across horizon, no begin column")
            return GateResult("review", 0.7, reasons)

    return GateResult("auto_accept", 1.0, reasons or ["clean"])
