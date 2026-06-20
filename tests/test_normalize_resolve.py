from directory.domain import Prayer
from directory.ingest.normalize import (
    normalize_token,
    resolve_kind,
    resolve_prayer,
)


def test_normalize_token_strips_punct_and_diacritics():
    assert normalize_token("Jumu'ah") == "jumuah"
    assert normalize_token("  Ẓuhr-Begins ") == "zuhr begins"


def test_exact_prayer_synonyms_are_high_confidence():
    for raw, expected in [
        ("Fajr", Prayer.FAJR),
        ("ZUHR", Prayer.DHUHR),
        ("Asar", Prayer.ASR),
        ("Maghreb", Prayer.MAGHRIB),
        ("Esha", Prayer.ISHA),
        ("Jummah", Prayer.JUMUAH),
    ]:
        m = resolve_prayer(raw)
        assert m.prayer == expected
        assert m.confidence == 1.0
        assert m.fuzzy is False


def test_prayer_word_inside_header():
    m = resolve_prayer("Fajr Iqamah")
    assert m.prayer == Prayer.FAJR
    assert m.confidence == 1.0


def test_fuzzy_prayer_is_low_confidence():
    m = resolve_prayer("Fajer")  # unseen spelling, close to fajr/fajar
    assert m.prayer == Prayer.FAJR
    assert m.fuzzy is True
    assert m.confidence < 1.0


def test_unknown_prayer_is_none():
    assert resolve_prayer("breakfast").prayer is None


def test_kind_resolution():
    assert resolve_kind("Iqamah").kind == "jamaah"
    assert resolve_kind("Jamaat").kind == "jamaah"
    assert resolve_kind("Begins").kind == "begin"
    assert resolve_kind("Adhan").kind == "begin"
    assert resolve_kind("weather").kind is None
