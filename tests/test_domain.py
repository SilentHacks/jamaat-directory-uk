import pytest

from directory.domain import DAILY_PRAYERS, Prayer


def test_values_are_lowercase_names():
    assert Prayer.FAJR.value == "fajr"
    assert Prayer.JUMUAH.value == "jumuah"


def test_daily_prayers_order_excludes_jumuah():
    assert DAILY_PRAYERS == (
        Prayer.FAJR,
        Prayer.DHUHR,
        Prayer.ASR,
        Prayer.MAGHRIB,
        Prayer.ISHA,
    )


def test_parse_case_insensitive():
    assert Prayer.parse("Fajr") is Prayer.FAJR
    assert Prayer.parse("ISHA") is Prayer.ISHA


def test_parse_unknown_raises():
    with pytest.raises(ValueError):
        Prayer.parse("tahajjud")
