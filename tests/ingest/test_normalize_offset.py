from directory.ingest.normalize import parse_offset


def test_signed_offsets():
    assert parse_offset("+5") == 5
    assert parse_offset("+ 5") == 5
    assert parse_offset("-10") == -10
    assert parse_offset("+90") == 90


def test_minutes_suffix_forms():
    assert parse_offset("+5 min") == 5
    assert parse_offset("+5mins") == 5
    assert parse_offset("+5 minutes") == 5
    assert parse_offset("5 min") == 5  # a minutes suffix makes the sign optional


def test_unicode_dash_and_arabic_digits():
    assert parse_offset("–5") == -5  # en dash
    assert parse_offset("−5") == -5  # minus sign
    assert parse_offset("+٥") == 5  # arabic-indic digit


def test_rejects_ambiguous_or_non_offsets():
    assert parse_offset("5") is None  # bare integer is too ambiguous
    assert parse_offset("06:20") is None
    assert parse_offset("closed") is None
    assert parse_offset("") is None
    assert parse_offset(None) is None
