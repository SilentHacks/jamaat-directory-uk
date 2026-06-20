from directory.ingest.extractors.config_schema import SourceConfig
from directory.ingest.extractors.engine import ExtractionResult
from directory.ingest.gates import lint_config, run_gates
from directory.ingest.materialize import OccurrenceRow

GRID_CFG = SourceConfig.from_json(
    """
    {"shape":"html_table","grid":{"columns":[
      {"kind":"jamaah","prayer":"fajr","index":1},
      {"kind":"jamaah","prayer":"dhuhr","index":2},
      {"kind":"jamaah","prayer":"asr","index":3},
      {"kind":"jamaah","prayer":"maghrib","index":4},
      {"kind":"jamaah","prayer":"isha","index":5}
    ]}}
    """
)


def _day(date_str, times):
    prayers = ["fajr", "dhuhr", "asr", "maghrib", "isha"]
    return [
        OccurrenceRow(date_str, p, 0, t, None, None)
        for p, t in zip(prayers, times, strict=True)
    ]


def test_clean_full_day_auto_accepts():
    occ = _day("2026-06-21", ["05:00", "13:30", "18:30", "21:30", "23:00"])
    occ += _day("2026-06-22", ["05:01", "13:30", "18:31", "21:31", "23:00"])
    res = run_gates(GRID_CFG, ExtractionResult(), occ)
    assert res.lane == "auto_accept"


def test_missing_prayer_auto_rejects():
    occ = _day("2026-06-21", ["05:00", "13:30", "18:30", "21:30", "23:00"])[:4]
    res = run_gates(GRID_CFG, ExtractionResult(), occ)
    assert res.lane == "auto_reject"
    assert any("missing" in r for r in res.reasons)


def test_non_monotonic_day_auto_rejects():
    occ = _day("2026-06-21", ["05:00", "13:30", "12:00", "21:30", "23:00"])  # asr < dhuhr
    res = run_gates(GRID_CFG, ExtractionResult(), occ)
    assert res.lane == "auto_reject"


def test_out_of_window_value_auto_rejects():
    occ = _day("2026-06-21", ["09:00", "13:30", "18:30", "21:30", "23:00"])  # fajr 09:00
    res = run_gates(GRID_CFG, ExtractionResult(), occ)
    assert res.lane == "auto_reject"


def test_self_match_failure_auto_rejects():
    occ = _day("2026-06-21", ["05:00", "13:30", "18:30", "21:30", "23:00"])
    occ += _day("2026-06-22", ["05:01", "13:30", "18:31", "21:31", "23:00"])
    res = run_gates(GRID_CFG, ExtractionResult(), occ, html_text="no times here at all")
    assert res.lane == "auto_reject"
    assert any("self-match" in r for r in res.reasons)


def test_empty_occurrences_auto_rejects():
    assert run_gates(GRID_CFG, ExtractionResult(), []).lane == "auto_reject"


def test_constant_columns_no_begin_routes_to_review():
    occ = []
    for day in range(21, 29):  # 8 distinct dates → >= 7
        occ += _day(f"2026-06-{day}", ["05:00", "13:30", "18:30", "21:30", "23:00"])
    res = run_gates(GRID_CFG, ExtractionResult(), occ)
    assert res.lane == "review"
    assert any("constant" in r for r in res.reasons)


def test_lint_flags_jamaah_column_without_prayer():
    bad = SourceConfig.from_json(
        '{"shape":"html_table","grid":{"columns":[{"kind":"jamaah","index":1}]}}'
    )
    assert lint_config(bad)
