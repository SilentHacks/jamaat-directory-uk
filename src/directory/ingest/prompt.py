from directory.domain import Prayer
from directory.ingest.discover import CandidateBundle

_PRAYERS = ", ".join(p.value for p in Prayer)

_SCHEMA_HINT = f"""\
Return ONE JSON object and nothing else (no prose, no code fences):

{{
  "url": "<the candidate page URL the timetable lives on>",
  "config": {{
    "shape": "html_table" | "html_repeated" | "rules" | "widget",
    "grid": {{                       // required for html_table / html_repeated
      "table_selector": "<css>",     // html_table: CSS for the <table> (optional)
      "row_selector": "<css>",       // html_repeated: CSS for each day item
      "transpose": false,            // true if prayers are rows and days are columns
      "date": {{"index": 0, "selector": "<css>", "format": null}},
      "columns": [
        {{"kind": "jamaah", "prayer": "fajr", "index": 1,
          "selector": "<css>", "header_seen": "<raw header text>"}}
      ]
    }},
    "jumuah": {{                      // optional weekly Friday block
      "source": "fixed",
      "sessions": [{{"label": "1st Jumu'ah", "time": "13:00"}}]
    }},
    "rules": {{"rules": [{{"prayer": "fajr", "fixed": "05:00"}}]}}
  }}
}}

Rules:
- "prayer" is exactly one of: {_PRAYERS}.
- html_table columns use a 0-based "index"; html_repeated columns use a CSS "selector".
- "kind" is "jamaah" (congregation / iqamah) or "begin" (adhan / start time).
- Copy the raw column header text into "header_seen" for every column.
- Use "rules" only for fixed times; "widget" only for embedded prayer-time widgets.
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
