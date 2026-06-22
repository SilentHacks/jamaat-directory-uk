from datetime import date

from directory.ingest.extractors.engine import extract
from directory.ingest.extractors.platforms.generic_table import GenericTableDetector
from directory.ingest.gates import run_gates
from directory.ingest.materialize import materialize
from tests.conftest import FIXTURES

HTML = (FIXTURES / "haywardsheath.html").read_text(encoding="utf-8")


def test_haywardsheath_detects_extracts_and_auto_accepts():
    today = date(2026, 6, 22)

    match = GenericTableDetector().detect(HTML, "https://www.haywardsheathmosque.co.uk/")
    assert match is not None
    assert match.platform == "generic_table"
    assert match.requires_js is False
    g = match.config.grid
    assert g.prayer_label_index == 0
    assert g.single_day is True
    assert g.table_selector == "table.w-full"  # class[0]

    result = extract(HTML, match.config, year=today.year, month=today.month, today=today)
    rows = materialize(result, match.config, horizon_start=today, horizon_end=today)

    gate = run_gates(match.config, result, rows, html_text=HTML)
    assert gate.lane == "auto_accept"

    # Five daily prayers (Sunrise dropped), all for today, begin + jamaah captured.
    by = {r.prayer: r for r in rows}
    assert set(by) == {"fajr", "dhuhr", "asr", "maghrib", "isha"}
    assert all(r.date == today.isoformat() for r in rows)
    assert by["fajr"].begin_time == "02:50" and by["fajr"].jamaah_time == "03:45"
    assert by["isha"].begin_time == "22:39" and by["isha"].jamaah_time == "23:00"
