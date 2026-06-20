import re

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
