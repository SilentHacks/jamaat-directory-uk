from directory.ingest.extractors.config_schema import SourceConfig
from directory.ingest.extractors.engine import ExtractionResult
from directory.ingest.gates import JUMUAH_MISSING, lint_config, run_gates
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


def _jumuah(date_str, times):
    return [
        OccurrenceRow(date_str, "jumuah", i + 1, t, None, f"sess {i+1}")
        for i, t in enumerate(times)
    ]


def test_clean_full_day_with_jumuah_auto_accepts_no_flag():
    occ = _day("2026-06-21", ["05:00", "13:30", "18:30", "21:30", "23:00"])
    occ += _day("2026-06-22", ["05:01", "13:30", "18:31", "21:31", "23:00"])
    occ += _jumuah("2026-06-26", ["13:00"])
    res = run_gates(GRID_CFG, ExtractionResult(), occ)
    assert res.lane == "auto_accept"
    assert res.flags == []


def test_full_day_no_jumuah_auto_accepts_with_flag():
    occ = _day("2026-06-21", ["05:00", "13:30", "18:30", "21:30", "23:00"])
    occ += _day("2026-06-22", ["05:01", "13:30", "18:31", "21:31", "23:00"])
    res = run_gates(GRID_CFG, ExtractionResult(), occ)
    assert res.lane == "auto_accept"
    assert res.flags == [JUMUAH_MISSING]


def test_missing_prayer_routes_to_review():
    occ = _day("2026-06-21", ["05:00", "13:30", "18:30", "21:30", "23:00"])[:4]  # no isha
    res = run_gates(GRID_CFG, ExtractionResult(), occ)
    assert res.lane == "review"
    assert any("incomplete" in r and "isha" in r for r in res.reasons)


def test_inconsistent_completeness_across_dates_routes_to_review():
    occ = _day("2026-06-21", ["05:00", "13:30", "18:30", "21:30", "23:00"])  # full
    occ += _day("2026-06-22", ["05:01", "13:30", "18:31", "21:31", "23:00"])[:3]  # partial
    res = run_gates(GRID_CFG, ExtractionResult(), occ)
    assert res.lane == "review"
    assert any("incomplete" in r for r in res.reasons)


def test_only_jumuah_no_daily_routes_to_review():
    occ = _jumuah("2026-06-26", ["13:00", "13:45"])
    res = run_gates(GRID_CFG, ExtractionResult(), occ)
    assert res.lane == "review"
    assert any("only jumuah" in r for r in res.reasons)


def test_non_monotonic_day_auto_rejects():
    occ = _day("2026-06-21", ["05:00", "13:30", "12:00", "21:30", "23:00"])  # asr < dhuhr
    res = run_gates(GRID_CFG, ExtractionResult(), occ)
    assert res.lane == "auto_reject"


def test_partial_day_still_window_checked():
    # Only fajr present but at an implausible hour → reject, not review.
    occ = [OccurrenceRow("2026-06-21", "fajr", 0, "09:00", None, None)]
    res = run_gates(GRID_CFG, ExtractionResult(), occ)
    assert res.lane == "auto_reject"


def test_out_of_window_value_auto_rejects():
    occ = _day("2026-06-21", ["09:00", "13:30", "18:30", "21:30", "23:00"])  # fajr 09:00
    res = run_gates(GRID_CFG, ExtractionResult(), occ)
    assert res.lane == "auto_reject"


def test_malformed_jumuah_auto_rejects():
    occ = _day("2026-06-21", ["05:00", "13:30", "18:30", "21:30", "23:00"])
    occ += _jumuah("2026-06-26", ["13:00", "12:30"])  # sessions not ordered
    res = run_gates(GRID_CFG, ExtractionResult(), occ)
    assert res.lane == "auto_reject"
    assert any("jumuah" in r for r in res.reasons)


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
