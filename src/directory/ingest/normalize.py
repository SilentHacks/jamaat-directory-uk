import difflib
import re
import unicodedata
from dataclasses import dataclass
from datetime import date

from directory.domain import Prayer

_ARABIC_INDIC = str.maketrans(
    "٠١٢٣٤٥٦٧٨٩"
    "۰۱۲۳۴۵۶۷۸۹",
    "01234567890123456789",
)

_TIME_RE = re.compile(r"(\d{1,2})\s*[:.٫]\s*(\d{2})\s*([ap]\.?m\.?)?", re.IGNORECASE)


def _ascii_digits(s: str) -> str:
    return s.translate(_ARABIC_INDIC)


def parse_time(raw: str | None, *, prefer_pm: bool | None = None) -> str | None:
    if not raw:
        return None
    s = _ascii_digits(str(raw)).strip().lower()
    m = _TIME_RE.search(s)
    if not m:
        return None
    hh, mm, ap = int(m.group(1)), int(m.group(2)), m.group(3)
    if mm > 59:
        return None
    if ap:
        ap = ap.replace(".", "")
        if ap == "pm" and hh != 12:
            hh += 12
        elif ap == "am" and hh == 12:
            hh = 0
    elif prefer_pm is True and 1 <= hh <= 11:
        hh += 12
    elif prefer_pm is False and hh == 12:
        hh = 0
    if hh > 23:
        return None
    return f"{hh:02d}:{mm:02d}"


_MONTH_NAMES = [
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
]
_MONTHS: dict[str, int] = {}
for _i, _name in enumerate(_MONTH_NAMES, start=1):
    _MONTHS[_name] = _i
    _MONTHS[_name[:3]] = _i

_ISO_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_DMY_RE = re.compile(r"\b(\d{1,2})[/.\-](\d{1,2})(?:[/.\-](\d{2,4}))?\b")
_DAY_MONTH_RE = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]{3,})\b")
_MONTH_DAY_RE = re.compile(r"\b([a-z]{3,})\s+(\d{1,2})(?:st|nd|rd|th)?\b")
_DAY_ONLY_RE = re.compile(r"^\s*(\d{1,2})(?:st|nd|rd|th)?\s*$")


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_date(raw: str | None, *, year: int, month: int | None = None) -> date | None:
    if not raw:
        return None
    s = _ascii_digits(str(raw)).strip().lower()
    if not s:
        return None

    m = _ISO_RE.search(s)
    if m:
        return _safe_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))

    m = _DMY_RE.search(s)
    if m:
        day, mon = int(m.group(1)), int(m.group(2))
        yr = m.group(3)
        if yr is not None:
            yr_i = int(yr)
            if yr_i < 100:
                yr_i += 2000
        else:
            yr_i = year
        return _safe_date(yr_i, mon, day)

    m = _DAY_MONTH_RE.search(s)
    if m and m.group(2) in _MONTHS:
        return _safe_date(year, _MONTHS[m.group(2)], int(m.group(1)))

    m = _MONTH_DAY_RE.search(s)
    if m and m.group(1) in _MONTHS:
        return _safe_date(year, _MONTHS[m.group(1)], int(m.group(2)))

    m = _DAY_ONLY_RE.match(s)
    if m and month is not None:
        return _safe_date(year, month, int(m.group(1)))

    return None


_PUNCT_REMOVE_RE = re.compile(r"[''\.()]")
_PUNCT_SPACE_RE = re.compile(r"[-/]")

_PRAYER_SYNONYMS: dict[Prayer, set[str]] = {
    Prayer.FAJR: {
        "fajr", "fajar", "fadjr", "fjr", "subh", "صلاة الفجر",
        "الفجر", "فجر"
    },
    Prayer.DHUHR: {
        "dhuhr", "zuhr", "duhr", "zohr", "zuhar", "duhar", "zuhur",
        "الظهر", "ظهر"
    },
    Prayer.ASR: {"asr", "asar", "العصر", "عصر"},
    Prayer.MAGHRIB: {"maghrib", "magrib", "maghreb", "mughrib", "المغرب", "مغرب"},
    Prayer.ISHA: {"isha", "esha", "ishaa", "eshaa", "isyak", "العشاء", "عشاء"},
    Prayer.JUMUAH: {
        "jumuah", "jumma", "jummah", "juma", "jumah", "jumua",
        "friday", "الجمعة", "جمعة"
    },
}

_KIND_SYNONYMS: dict[str, set[str]] = {
    "jamaah": {
        "jamaah", "jamaat", "iqamah", "iqaamah", "iqama", "iqamat",
        "iqaama", "congregation", "salah", "salat", "prayer",
    },
    "begin": {"begin", "begins", "start", "starts", "adhan", "athan", "azan", "beginning"},
}


def normalize_token(raw: str) -> str:
    s = unicodedata.normalize("NFKD", str(raw))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    s = _PUNCT_REMOVE_RE.sub("", s)
    s = _PUNCT_SPACE_RE.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _build_lookup(synonyms: dict) -> dict[str, object]:
    out: dict[str, object] = {}
    for key, words in synonyms.items():
        for w in words:
            out[normalize_token(w)] = key
    return out


_PRAYER_LOOKUP = _build_lookup(_PRAYER_SYNONYMS)
_KIND_LOOKUP = _build_lookup(_KIND_SYNONYMS)


@dataclass
class PrayerMatch:
    prayer: Prayer | None
    confidence: float
    fuzzy: bool


@dataclass
class KindMatch:
    kind: str | None
    confidence: float
    fuzzy: bool


def _resolve(raw: str, lookup: dict) -> tuple[object | None, float, bool]:
    norm = normalize_token(raw)
    if not norm:
        return None, 0.0, False
    if norm in lookup:
        return lookup[norm], 1.0, False
    words = norm.split()
    for w in words:
        if w in lookup:
            return lookup[w], 1.0, False
    candidates = list(lookup)
    for token in [norm, *words]:
        close = difflib.get_close_matches(token, candidates, n=1, cutoff=0.8)
        if close:
            return lookup[close[0]], 0.6, True
    return None, 0.0, False


def resolve_prayer(raw: str) -> PrayerMatch:
    value, conf, fuzzy = _resolve(raw, _PRAYER_LOOKUP)
    return PrayerMatch(value, conf, fuzzy)  # type: ignore[arg-type]


def resolve_kind(raw: str) -> KindMatch:
    value, conf, fuzzy = _resolve(raw, _KIND_LOOKUP)
    return KindMatch(value, conf, fuzzy)  # type: ignore[arg-type]
