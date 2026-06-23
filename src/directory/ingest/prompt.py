from directory.domain import Prayer
from directory.ingest.discover import CandidateBundle

_PRAYERS = ", ".join(p.value for p in Prayer)

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
      "transpose": false,            // true if prayers are ROWS and DATES are columns
      "prayer_label_index": null,    // prayer-rows layout: column holding the prayer name
      "single_day": false,           // true if the table shows only today (no date column)
      "date": {{"index": 0, "selector": "<css>", "format": null}},
      "columns": [
        {{"kind": "jamaah", "prayer": "fajr", "index": 1,
          "selector": "<css>", "header_seen": "<raw header text>"}},
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
- html_table columns use a 0-based "index"; html_repeated columns use a CSS "selector".
- "kind" is "jamaah" (congregation / iqamah) or "begin" (adhan / start time).
- If prayers run DOWN the rows (a label column) and the header names the kind
  (Begin/Iqamah), set "prayer_label_index" to that label column and leave each
  column's "prayer" null — it is taken from the row label.
- If the table shows only today's times with no date column, set "single_day": true
  (omit "date"); it is re-read every day.
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
    bundle: CandidateBundle, *, max_region_chars: int = 4000, max_candidates: int = 3
) -> str:
    parts = [
        "You map a UK mosque's congregational (jamaah) prayer timetable into an "
        "extraction config.",
        f"Mosque website: {bundle.base_url}",
        "",
        "Candidate page regions (most likely first):",
    ]
    for i, c in enumerate(bundle.candidates[:max_candidates], start=1):
        parts.append(f"\n--- candidate {i}: {c.url} ---")
        parts.append(c.region_html[:max_region_chars])
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
widget — 0-based "index" for html_table, CSS "selector" for html_repeated).

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
