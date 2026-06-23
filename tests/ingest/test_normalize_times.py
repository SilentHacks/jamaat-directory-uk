from directory.ingest.normalize import parse_times


def test_returns_each_time_in_order():
    # A single cell that packs a begin + iqamah time, separated by a label.
    assert parse_times("2:55 AM Iqm 3:45 AM", prefer_pm=False) == ["02:55", "03:45"]


def test_prefer_pm_applies_to_every_time():
    assert parse_times("9:15 Iqm 9:20", prefer_pm=True) == ["21:15", "21:20"]


def test_single_time_is_a_one_element_list():
    assert parse_times("4:47 AM", prefer_pm=False) == ["04:47"]


def test_no_times_is_empty_list():
    assert parse_times("closed", prefer_pm=None) == []
    assert parse_times(None, prefer_pm=None) == []


def test_keeps_duplicates_and_order():
    assert parse_times("13:30 13:30 14:00", prefer_pm=None) == ["13:30", "13:30", "14:00"]
