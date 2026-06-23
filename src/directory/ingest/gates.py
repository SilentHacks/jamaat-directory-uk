from collections import defaultdict
from dataclasses import dataclass, field

from directory.domain import DAILY_PRAYERS
from directory.ingest.extractors.config_schema import SourceConfig
from directory.ingest.extractors.engine import ExtractionResult
from directory.ingest.materialize import OccurrenceRow
from directory.ingest.normalize import source_time_values

# Plausible jamaah windows in minutes-from-midnight, inclusive.
_WINDOWS: dict[str, tuple[int, int]] = {
    # Floor 00:30: high-latitude UK summer Fajr (e.g. Aberdeen, 57°N) is genuinely
    # ~01:00–01:30 near the solstice. The monotonic check still guards ordering.
    "fajr": (30, 7 * 60 + 30),
    "dhuhr": (11 * 60 + 30, 15 * 60),
    # Ceiling 20:30: UK high-summer Hanafi Asr begins late afternoon and the
    # jamaah is well after (London late June reaches ~20:00). The monotonic check
    # and the Maghrib window still enforce Asr < Maghrib, so the wider ceiling
    # admits genuine summer data without disordering the day.
    "asr": (13 * 60, 20 * 60 + 30),
    "maghrib": (15 * 60 + 30, 22 * 60 + 30),
    "isha": (17 * 60, 23 * 60 + 59),
    "jumuah": (12 * 60, 15 * 60),
}
_DAILY = [p.value for p in DAILY_PRAYERS]

JUMUAH_MISSING = "jumuah_missing"


@dataclass
class GateResult:
    lane: str  # "auto_accept" | "review" | "auto_reject"
    confidence: float
    reasons: list[str]
    flags: list[str] = field(default_factory=list)


def _minutes(hhmm: str) -> int:
    hh, mm = (int(x) for x in hhmm.split(":"))
    return hh * 60 + mm


def lint_config(config: SourceConfig) -> list[str]:
    problems: list[str] = []
    grid = config.grid
    # dom_records is deliberately omitted: its prayer columns are induced from the
    # rendered stream at extract time, so an empty grid.columns is expected, not a
    # defect. dom_grid carries a normal html_table grid and is checked here.
    if config.shape in {"html_table", "html_repeated"}:
        if grid is None or not grid.columns:
            problems.append("grid shape has no columns")
        else:
            # In prayer-rows layout the prayer comes from the label column, so a
            # prayer-less jamaah column is expected, not a defect.
            row_labelled = grid.prayer_label_index is not None
            for col in grid.columns:
                if col.kind == "jamaah" and col.prayer is None and not row_labelled:
                    problems.append(f"jamaah column without prayer: {col!r}")
    problems.extend(_lint_paging(config))
    return problems


def _lint_paging(config: SourceConfig) -> list[str]:
    """Paging only makes sense for multi-day, date-bearing layouts. The single-day
    and prayer-rows layouts stamp the run date and ignore month/year, so paging
    them would merely re-stamp today; 'rules' scrapes no cells at all."""
    paging = config.paging
    if paging is None:
        return []
    problems: list[str] = []
    if config.shape == "rules":
        problems.append("paging not supported for 'rules' shape")
    grid = config.grid
    if grid is not None and (grid.single_day or grid.prayer_label_index is not None):
        problems.append("paging requires a multi-day date layout (not single_day/prayer-rows)")
    if grid is not None and grid.month_sections:
        problems.append("paging is redundant with month_sections (the page carries every month)")
    if paging.mode == "url_template":
        template = paging.url_template or ""
        if "{month" not in template:
            problems.append("paging url_template must vary by {month}")
        else:
            try:
                template.format(year=2000, month=1)
            except (KeyError, ValueError, IndexError) as exc:
                problems.append(f"paging url_template invalid: {exc}")
    return problems


def _has_jumuah(occ: list[OccurrenceRow]) -> bool:
    return any(o.prayer == "jumuah" for o in occ)


def _plausibility_failure(
    by_date: dict[str, dict[str, OccurrenceRow]],
) -> str | None:
    """Apply window + monotonic checks to *whatever* daily prayers are present on
    each date, in canonical order. Returns a reason string on the first failure."""
    for d, prayers in by_date.items():
        present = [p for p in _DAILY if p in prayers]
        mins = [_minutes(prayers[p].jamaah_time) for p in present]
        if mins != sorted(mins):
            return f"{d}: non-monotonic day"
        for p in present:
            lo, hi = _WINDOWS[p]
            if not (lo <= _minutes(prayers[p].jamaah_time) <= hi):
                return f"{d}: {p} out of window"
    return None


def jumuah_failure(occurrences: list[OccurrenceRow]) -> str | None:
    jum_by_date: dict[str, list[OccurrenceRow]] = defaultdict(list)
    for o in occurrences:
        if o.prayer == "jumuah":
            jum_by_date[o.date].append(o)
    for d, sessions in jum_by_date.items():
        if not (1 <= len(sessions) <= 4):
            return f"{d}: bad jumuah session count"
        times = [_minutes(s.jamaah_time) for s in sorted(sessions, key=lambda s: s.session_idx)]
        if times != sorted(times) or len(set(times)) != len(times):
            return f"{d}: jumuah sessions not ordered/distinct"
        lo, hi = _WINDOWS["jumuah"]
        if any(not (lo <= t <= hi) for t in times):
            return f"{d}: jumuah out of window"
    return None


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

    # Plausibility on whatever is present → implausible data is rejected.
    plaus = _plausibility_failure(by_date)
    if plaus is not None:
        return GateResult("auto_reject", 0.0, [plaus])

    # Self-extraction match: every scraped jamaah time must appear in the source.
    # Compared by value (not substring) so a 12h-format page's "6:00" matches a
    # materialized 18:00. Derived times (begin + offset) are computed, not present
    # verbatim, so they are exempt; their plausibility is still enforced by the
    # window/monotonic checks above and the begin they derive from is in the source.
    # Fixed/rules Jumu‘ah sessions come from the config block (e.g. a day-widget
    # authored separately from the daily grid's source), not from the scraped grid
    # HTML, so they are exempt too — their plausibility is enforced by jumuah_failure.
    jumuah_from_config = config.jumuah is not None and config.jumuah.source in {"fixed", "rules"}
    if html_text:
        present = source_time_values(html_text)
        for o in occurrences:
            if o.derived:
                continue
            if o.prayer == "jumuah" and jumuah_from_config:
                continue
            if o.jamaah_time not in present:
                return GateResult("auto_reject", 0.0, [f"self-match failed for {o.jamaah_time}"])

    # Jumu'ah plausibility (malformed sessions are always rejected).
    jum = jumuah_failure(occurrences)
    if jum is not None:
        return GateResult("auto_reject", 0.0, [jum])

    # Only jumuah, no daily at all → withhold for review (not served).
    if not daily:
        return GateResult("review", 0.5, [*reasons, "only jumuah, no daily"])

    # Completeness: which prayers are missing across the horizon?
    missing_by_date = {
        d: [p for p in _DAILY if p not in prayers] for d, prayers in by_date.items()
    }
    missing_prayers = sorted({p for miss in missing_by_date.values() for p in miss})
    if missing_prayers:
        reasons.append(f"incomplete: missing {missing_prayers}")
        return GateResult("review", 0.7, reasons)

    # Every date has all five daily prayers from here on.
    # Constant-column red flag → review (fixed iqamah is common, so soften to review).
    distinct_dates = len(by_date)
    if distinct_dates >= 7 and not has_begin:
        constant = all(
            len({by_date[d][p].jamaah_time for d in by_date}) == 1 for p in _DAILY
        )
        if constant:
            reasons.append("all daily prayers constant across horizon, no begin column")
            return GateResult("review", 0.7, reasons)

    flags: list[str] = []
    if not _has_jumuah(occurrences):
        flags.append(JUMUAH_MISSING)
    return GateResult("auto_accept", 1.0, reasons or ["clean"], flags)
