from directory.ingest.failure import (
    FailureKind,
    classify_failure,
    feedback_prompt_kind,
)


def test_classify_invalid_json():
    assert classify_failure("invalid config: no JSON object in harness output") == (
        FailureKind.INVALID_JSON
    )


def test_classify_lint():
    assert classify_failure("lint: grid shape has no columns") == FailureKind.LINT


def test_classify_zero_rows():
    assert classify_failure("no occurrences produced") == FailureKind.ZERO_ROWS


def test_classify_self_match():
    assert classify_failure("self-match failed for 05:00") == FailureKind.SELF_MATCH


def test_classify_plausibility_window():
    assert classify_failure("2026-06-01: fajr out of window") == FailureKind.PLAUSIBILITY


def test_classify_plausibility_monotonic():
    assert classify_failure("2026-06-01: non-monotonic day") == FailureKind.PLAUSIBILITY


def test_classify_incomplete():
    assert classify_failure("incomplete: missing ['isha']") == FailureKind.INCOMPLETE


def test_classify_fetch_empty():
    assert classify_failure("empty body") == FailureKind.FETCH_EMPTY
    assert classify_failure("render failed: TimeoutError") == FailureKind.FETCH_EMPTY


def test_classify_invalid_schema_last():
    # a pydantic-style validation error is schema, not json
    assert classify_failure("invalid config: 1 validation error for SourceConfig") == (
        FailureKind.INVALID_SCHEMA
    )


def test_classify_unknown_and_none():
    assert classify_failure("something weird") == FailureKind.UNKNOWN
    assert classify_failure(None) == FailureKind.UNKNOWN


def test_feedback_routes_table_failures_to_repair():
    assert feedback_prompt_kind(FailureKind.ZERO_ROWS, "table_repair") == "table_repair"
    assert feedback_prompt_kind(FailureKind.LINT, "unknown") == "table_repair"
    assert feedback_prompt_kind(FailureKind.PLAUSIBILITY, "table_choice") == "table_repair"


def test_feedback_routes_fetch_empty_to_terminal():
    assert feedback_prompt_kind(FailureKind.FETCH_EMPTY, "table_repair") == "terminal"


def test_feedback_keeps_kind_for_media_and_json():
    # a table-shaped failure on a media prompt does not become a table repair
    assert feedback_prompt_kind(FailureKind.ZERO_ROWS, "media") == "media"
    # invalid json just re-asks with the same kind
    assert feedback_prompt_kind(FailureKind.INVALID_JSON, "table_repair") == "table_repair"
