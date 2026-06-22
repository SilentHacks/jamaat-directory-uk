from datetime import date, timedelta

from directory.domain import DAILY_PRAYERS
from directory.ingest.extractors.engine import extract
from directory.ingest.extractors.platforms.generic_table import GenericTableDetector
from directory.ingest.gates import run_gates
from directory.ingest.materialize import materialize

URL = "https://m.example/old/prayer-timetable/"


def _month_table(month: str, day_rows: list[tuple]) -> str:
    # Azhar shape: a <td>-only table, month caption row, a grouped Begins/Jamah
    # header (Sunrise has only Begins), then day-numbered rows. Day and Sunrise
    # span both header rows via rowspan.
    rows = "".join(
        "<tr>" + "".join(f"<td>{c}</td>" for c in (str(day), *cells)) + "</tr>"
        for day, *cells in day_rows
    )
    return (
        "<table>"
        f"<tr><td colspan='12'>{month}</td></tr>"
        "<tr><td rowspan='2'>Day</td><td colspan='2'>Fajr</td>"
        "<td rowspan='2'>Sunrise</td><td colspan='2'>Zuhr</td>"
        "<td colspan='2'>Asr</td><td colspan='2'>Magrib</td><td colspan='2'>Isha</td></tr>"
        "<tr><td>Begins</td><td>Jamah</td><td>Begins</td><td>Jamah</td>"
        "<td>Begins</td><td>Jamah</td><td>Begins</td><td>Jamah</td>"
        "<td>Begins</td><td>Jamah</td></tr>"
        f"{rows}</table>"
    )


# Two plausible UK months (June-ish high sun, July-ish), 2 days each.
JUNE = _month_table(
    "June",
    [
        (1, "02:30 am", "03:15 am", "04:30 am", "01:00 pm", "01:30 pm",
         "05:30 pm", "06:00 pm", "09:15 pm", "09:30 pm", "10:45 pm", "11:00 pm"),
        (2, "02:31 am", "03:15 am", "04:31 am", "01:00 pm", "01:30 pm",
         "05:31 pm", "06:00 pm", "09:16 pm", "09:31 pm", "10:46 pm", "11:00 pm"),
    ],
)
JULY = _month_table(
    "July",
    [
        (1, "02:45 am", "03:30 am", "04:45 am", "01:05 pm", "01:35 pm",
         "05:35 pm", "06:05 pm", "09:10 pm", "09:25 pm", "10:40 pm", "10:55 pm"),
    ],
)
ANNUAL_PAGE = f"<html><body><h1>Prayer Timetable</h1>{JUNE}{JULY}</body></html>"


def test_detects_month_section_layout():
    match = GenericTableDetector().detect(ANNUAL_PAGE, URL)
    assert match is not None
    assert match.platform == "generic_table"
    assert match.config.grid.month_sections is True
    assert match.config.grid.date.format == "day_only"
    pairs = {(c.prayer, c.kind) for c in match.config.grid.columns}
    for p in DAILY_PRAYERS:
        assert (p, "begin") in pairs and (p, "jamaah") in pairs


def test_day_column_is_the_date_index():
    match = GenericTableDetector().detect(ANNUAL_PAGE, URL)
    assert match.config.grid.date.index == 0


def test_single_month_table_is_not_hijacked():
    # One month label only → the multi-month guard does not fire; the ordinary
    # per-table path owns it (no month_sections config).
    one = f"<html><body>{JUNE}</body></html>"
    match = GenericTableDetector().detect(one, URL)
    assert match is None or not match.config.grid.month_sections


def test_extracts_to_auto_accept_end_to_end():
    today = date(2026, 6, 1)
    match = GenericTableDetector().detect(ANNUAL_PAGE, URL)
    cfg = match.config
    result = extract(ANNUAL_PAGE, cfg, year=today.year, month=today.month, today=today)
    rows = materialize(result, cfg, horizon_start=today, horizon_end=today + timedelta(days=60))
    gate = run_gates(cfg, result, rows, html_text=ANNUAL_PAGE)
    assert gate.lane == "auto_accept", gate.reasons
    # Both June days and July 1 fall in the 60-day horizon from June 1.
    assert {r.date for r in rows if r.prayer == "fajr"} == {
        "2026-06-01", "2026-06-02", "2026-07-01"
    }
