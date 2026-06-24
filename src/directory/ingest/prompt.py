import re

from directory.domain import Prayer
from directory.ingest.discover import CandidateBundle

_PRAYERS = ", ".join(p.value for p in Prayer)

_CLOCK = re.compile(r"\b\d{1,2}[:.]\d{2}\b")


def _window_region(html: str, budget: int, *, margin: int = 800) -> str:
    """Return a ``budget``-char slice of ``html`` centred on the densest cluster of
    clock times, so a timetable buried past the budget (after a long header/nav) is
    still surfaced. Falls back to the leading slice when no clock times are present.

    ``strip_to_region`` does not always isolate the table tightly — the first prayer
    time can sit 10k+ chars in — and a fixed leading slice then cuts it off."""
    if len(html) <= budget:
        return html
    positions = [m.start() for m in _CLOCK.finditer(html)]
    if not positions:
        return html[:budget]
    best_start, best_count = 0, -1
    for p in positions:
        start = max(0, p - margin)
        end = start + budget
        count = sum(1 for q in positions if start <= q < end)
        if count > best_count:
            best_count, best_start = count, start
    return html[best_start : best_start + budget]

_SCHEMA_HINT = f"""\
Return ONE JSON object and nothing else (no prose, no code fences):

{{
  "url": "<the candidate page URL the timetable lives on>",
  "config": {{
    "shape": "html_table" | "html_repeated" | "rules" | "widget" | "image" | "pdf",
    "media": {{                      // required for image / pdf
      "url": "<direct URL of the image or PDF timetable>"
    }},
    "grid": {{                       // required for html_table / html_repeated
      "table_selector": "<css>",     // html_table: CSS for the <table> (optional)
      "row_selector": "<css>",       // html_repeated: CSS for each day item
      "transpose": false,            // true if the PRAYER NAMES run across the top columns
      "prayer_label_index": null,    // prayer-rows layout: column holding the prayer name
      "single_day": false,           // true if the table shows only today (no date column)
      "date": {{"index": 0, "selector": "<css>", "format": null}},
      "columns": [
        {{"kind": "jamaah", "prayer": "fajr", "index": 1,
          "selector": "<css>", "header_seen": "<raw header text>"}},
        {{"kind": "begin", "prayer": "dhuhr", "index": 3, "time_index": 0}},
        {{"kind": "jamaah", "prayer": "dhuhr", "index": 3, "time_index": 1}},
        {{"kind": "jamaah", "prayer": "isha", "index": 9,
          "value_kind": "offset", "base_prayer": "isha", "header_seen": "Jamā‘ah"}}
      ]
    }},
    "jumuah": {{                      // optional weekly Friday block
      "source": "fixed",
      "sessions": [{{"label": "1st Jumu'ah", "time": "13:00"}}]
    }},
    "rules": {{"rules": [{{"prayer": "fajr", "fixed": "05:00"}}]}},
    "paging": {{                      // optional: timetable split across months
      "mode": "url_template",         // dynamic per-month path
      "url_template": "https://x.org/timetable/{{year}}/{{month:02d}}"
    }}
  }}
}}

Rules:
- "prayer" is exactly one of: {_PRAYERS}.
- READ THE ORIENTATION FIRST, then map it to the config:
  (a) Where are the PRAYER NAMES — across the top as column headers, or down a
      left-hand label column (one prayer per row)?
  (b) What runs DOWN the rows — dates/days (a multi-day timetable), or just a
      couple of kind rows like "Begins"/"Jamā‘ah" (a single-day widget)?
  * Prayer names across the top + a DATE column → default columns layout: set
    "date" and one column per prayer (by "index").
  * Prayer names across the top + a single row of times (today only) →
    "single_day": true (omit "date"); re-read every day.
  * Prayer names across the top + the rows are kind labels (Begins / Jamā‘ah)
    with NO date column (a daily widget) → set "transpose": true so prayers
    become rows, then "prayer_label_index": 0 with one column per kind ("begin" /
    "jamaah") at that kind row's index.
  * Prayer names down a left label column + the header names the kind
    (Begin/Iqamah) → set "prayer_label_index" to that label column and leave each
    column's "prayer" null — it is taken from the row label.
- "transpose": true whenever the PRAYER NAMES run across the top as columns and
  what runs down the side is NOT dates; it flips the grid so each row is one
  prayer, then read it as a prayer-label table.
- html_table columns use a 0-based "index"; html_repeated columns use a CSS "selector".
  An html_table cell cannot be sub-selected by CSS — if ONE cell packs two clock
  times (a begin and an iqamah in the SAME cell, e.g. "2:55 AM Iqm 3:45 AM"), add
  TWO columns at the SAME "index" and set "time_index" to pick each by position:
  0 = first time (begin), 1 = second (iqamah). Do not use "selector" to split a
  packed html_table cell.
- Use "time_index" ONLY for a single cell that literally holds two clock times.
  If "begin" and "jamaah" sit in SEPARATE rows or SEPARATE columns, that is a
  structural layout (transpose / prayer_label_index / two columns at different
  indices) — NEVER time_index.
- "kind" is "jamaah" (congregation / iqamah) or "begin" (adhan / start time).
- Use "paging" ONLY when one page shows a single month and a different month
  lives at a different URL (e.g. /2026/07). Set "mode":"url_template" and a
  "url_template" with "{{year}}" and "{{month}}" (or "{{month:02d}}") placeholders.
  Paging needs a real date column — never combine it with single_day / a
  prayer-rows layout / "rules".
- Copy the raw column header text into "header_seen" for every column.
- If a jamaah cell is a relative offset like "+5" / "+10 min" (minutes after a
  begin time), set "value_kind": "offset" on that column. The offset resolves
  against "base_prayer"'s begin time on the same day (default: the column's own
  prayer) — so the table MUST also have a "begin" column for that base prayer.
- Use "rules" only for fixed times; "widget" only for embedded prayer-time widgets.
- Always PREFER a real HTML/structured timetable. Use "image" or "pdf" ONLY when
  the daily timetable in every candidate region is published solely as an image
  (JPG/PNG) or a PDF — common for monthly printable timetables — and no HTML
  table/list of daily times exists anywhere in the candidates. Then set
  "shape":"image" (or "pdf") and "media":{{"url":"<direct image/PDF URL>"}}, and
  still include any "jumuah" block you can read from the HTML. The image/PDF
  itself is read later; never invent a "rules" config to stand in for one.
- Times are 24-hour "HH:MM".
"""


def build_author_prompt(
    bundle: CandidateBundle, *, max_region_chars: int = 6000, max_candidates: int = 5
) -> str:
    parts = [
        "You map a UK mosque's congregational (jamaah) prayer timetable into an "
        "extraction config.",
        f"Mosque website: {bundle.base_url}",
        "",
        "Each region below is a windowed excerpt centred on the prayer times. If a "
        "region is clearly truncated or insufficient, you MAY use the WebFetch tool "
        "to retrieve the candidate's live page before authoring the config.",
        "",
        "Candidate page regions (most likely first):",
    ]
    for i, c in enumerate(bundle.candidates[:max_candidates], start=1):
        parts.append(f"\n--- candidate {i}: {c.url} ---")
        parts.append(_window_region(c.region_html, max_region_chars))
    parts.append("")
    parts.append(_SCHEMA_HINT)
    return "\n".join(parts)


_BROWSE_SCHEMA_HINT = f"""\
You may navigate the live website to locate the timetable. Return ONE JSON object
and nothing else (no prose, no code fences).

For a standard layout, return:
{{
  "url": "<the page the timetable lives on>",
  "config": {{ "shape": "html_table" | "html_repeated" | "rules" | "widget", ... }}
}}
(use the same config fields as the single-shot schema: grid/columns, jumuah, rules,
widget — 0-based "index" for html_table, CSS "selector" for html_repeated). Read the
ORIENTATION first: if the PRAYER NAMES run across the top columns and the rows are
dates, use the default columns layout; if they run across the top but the rows are
kind labels (Begins/Jamā‘ah) or it is a single-day widget, set "transpose": true and
read it as a prayer-label table; if prayers run down a left label column, set
"prayer_label_index". Only when ONE html_table cell literally packs two clock times
(begin + iqamah in the same cell) add two columns at the SAME "index" with
"time_index" 0 (begin) and 1 (iqamah) — never a CSS selector, and never for begin /
jamaah that already sit in separate rows or columns.

If the daily timetable is published ONLY as an image (JPG/PNG) or a PDF, first keep
looking — check other pages, embedded widgets, and linked prayer-time pages — for an
HTML version. Only when no HTML/structured daily timetable exists anywhere on the
site, return:
{{
  "url": "<the page the timetable lives on>",
  "config": {{
    "shape": "image" | "pdf",
    "media": {{"url": "<direct URL of the image or PDF timetable>"}},
    "jumuah": {{ ... }}   // optional: any Friday times you can read from HTML
  }}
}}
The image/PDF is read in a later phase; do NOT force it into "rules"/"html_table".

Only when no standard shape fits a genuinely unique site, return a bespoke module:
{{
  "url": "<the timetable page>",
  "config": {{"shape": "bespoke", "bespoke": {{"module": "<snake_case_key>"}}}},
  "module_code": "<a self-contained Python module>"
}}
The module MUST, at import, call register_bespoke("<snake_case_key>", fn) where
fn(html, *, year, month) returns an ExtractionResult of Cell(date, prayer, kind,
time) rows. Import what you need from:
  from directory.ingest.extractors.bespoke import register_bespoke
  from directory.ingest.extractors.engine import Cell, ExtractionResult
  from directory.domain import Prayer
  from directory.ingest.normalize import parse_date, parse_time

If the timetable shows one month at a time, add a "paging" block to "config" so
every month in the horizon is crawled:
- Dynamic per-month URL (e.g. /2026/07): "paging": {{"mode":"url_template",
  "url_template":"https://x.org/{{year}}/{{month:02d}}"}} (use "{{year}}" and
  "{{month}}"/"{{month:02d}}").
- JS calendar you must click to change month: "paging": {{"mode":"render_nav",
  "nav": {{...}}}} where "nav" is either
    {{"kind":"next","next_selector":"<css for the next-month control>",
      "ready_selector":"<css that appears once the new month loads>"}}
  or, for a month picker,
    {{"kind":"select","month_select":"<css for the month <select>>",
      "year_select":"<css for the year <select>, if any>"}}.
  "select" assumes the month dropdown lists January–December in order; prefer
  "kind":"next" when unsure.
Paging needs a real date column — never combine it with single_day / a
prayer-rows layout / "rules".

Rules:
- "prayer" is exactly one of: {_PRAYERS}.
- "kind" is "jamaah" (congregation / iqamah) or "begin" (adhan / start time).
- A jamaah column of relative offsets ("+5") uses "value_kind": "offset" and
  resolves against "base_prayer"'s begin time (default: its own prayer).
- If prayers run down the rows, set "prayer_label_index" to the label column and
  leave columns' "prayer" null. If the table shows only today (no date column),
  set "single_day": true and omit "date".
- Times are 24-hour "HH:MM".
- Output exactly {{}} only when you find NO timetable at all (not even an image or
  PDF) within your budget. A timetable that exists only as an image/PDF is an
  "image"/"pdf" config, not {{}}.
"""


def build_browse_prompt(bundle: CandidateBundle) -> str:
    return "\n".join(
        [
            "You are a browsing agent. Find a UK mosque's congregational (jamaah) "
            "prayer timetable on its live website and map it into an extraction config.",
            f"Mosque website: {bundle.base_url}",
            "",
            _BROWSE_SCHEMA_HINT,
        ]
    )
