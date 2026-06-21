from datetime import date

from directory.ingest.normalize import parse_date


def test_iso():
    assert parse_date("2026-06-21", year=2026) == date(2026, 6, 21)


def test_dd_mm_and_dd_mm_yyyy():
    assert parse_date("21/06", year=2026) == date(2026, 6, 21)
    assert parse_date("21-06-2027", year=2026) == date(2027, 6, 21)


def test_day_month_words():
    assert parse_date("1 June", year=2026) == date(2026, 6, 1)
    assert parse_date("1st June", year=2026) == date(2026, 6, 1)
    assert parse_date("June 1", year=2026) == date(2026, 6, 1)
    assert parse_date("3 Jul", year=2026) == date(2026, 7, 3)


def test_day_only_uses_month_context():
    assert parse_date("21", year=2026, month=6) == date(2026, 6, 21)
    assert parse_date("21", year=2026) is None  # no month context


def test_unresolvable_returns_none():
    assert parse_date("Friday", year=2026) is None
    assert parse_date("", year=2026) is None
    assert parse_date(None, year=2026) is None
    assert parse_date("32", year=2026, month=6) is None
