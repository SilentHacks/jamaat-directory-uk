from directory.ingest.normalize import month_from_text


def test_bare_month_names():
    assert month_from_text("January") == 1
    assert month_from_text("February") == 2
    assert month_from_text("December") == 12


def test_three_letter_and_case_insensitive():
    assert month_from_text("Feb") == 2
    assert month_from_text("FEBRUARY") == 2
    assert month_from_text("sep") == 9


def test_month_with_trailing_year():
    assert month_from_text("January 2026") == 1
    assert month_from_text("Jan 2026") == 1


def test_day_number_is_not_a_month_label():
    # A day+month cell is a date (handled by parse_date), not a section label.
    assert month_from_text("1 February") is None
    assert month_from_text("21st March") is None


def test_non_month_text():
    assert month_from_text("Day") is None
    assert month_from_text("Sunrise") is None
    assert month_from_text("") is None
    assert month_from_text(None) is None
