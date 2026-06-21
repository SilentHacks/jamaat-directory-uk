from directory.ingest.normalize import parse_time


def test_24h_colon():
    assert parse_time("13:05") == "13:05"


def test_12h_pm_marker():
    assert parse_time("1:05 pm") == "13:05"
    assert parse_time("12:30am") == "00:30"
    assert parse_time("12:30 PM") == "12:30"


def test_dotted_separator_and_whitespace():
    assert parse_time(" 5.45 ") == "05:45"


def test_arabic_indic_digits():
    assert parse_time("٤:٣٠") == "04:30"


def test_prefer_pm_inference_when_no_marker():
    assert parse_time("1:15", prefer_pm=True) == "13:15"
    assert parse_time("5:00", prefer_pm=False) == "05:00"


def test_garbage_returns_none():
    assert parse_time("closed") is None
    assert parse_time(None) is None
    assert parse_time("25:00") is None
    assert parse_time("10:75") is None
