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


def test_weekday_plus_day_uses_month_context():
    # Monthly tables often label the date column "Mon 1", "Tue 2", ... — a
    # weekday word plus a day-of-month, with the month coming from context.
    assert parse_date("Mon 1", year=2026, month=6) == date(2026, 6, 1)
    assert parse_date("Tue 2", year=2026, month=6) == date(2026, 6, 2)
    assert parse_date("Sunday 30", year=2026, month=6) == date(2026, 6, 30)
    assert parse_date("Fri 13th", year=2026, month=3) == date(2026, 3, 13)
    assert parse_date("Mon 1", year=2026) is None  # still needs a month context


def test_day_plus_weekday_uses_month_context():
    # Some timetables reverse the label to "1 Mon", "2 Tue", occasionally without
    # a space ("1Mon"); the weekday is decorative and the month comes from context.
    assert parse_date("1 Mon", year=2026, month=6) == date(2026, 6, 1)
    assert parse_date("2 Tue", year=2026, month=6) == date(2026, 6, 2)
    assert parse_date("1Mon", year=2026, month=6) == date(2026, 6, 1)
    assert parse_date("1 Mon", year=2026) is None


def test_weekday_alone_is_not_a_date():
    # A bare weekday (no day number) carries no date.
    assert parse_date("Monday", year=2026, month=6) is None
    assert parse_date("Friday", year=2026, month=6) is None


def test_unresolvable_returns_none():
    assert parse_date("Friday", year=2026) is None
    assert parse_date("", year=2026) is None
    assert parse_date(None, year=2026) is None
    assert parse_date("32", year=2026, month=6) is None
