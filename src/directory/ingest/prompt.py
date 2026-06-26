import re
from enum import StrEnum

from directory.domain import Prayer
from directory.ingest.discover import CandidateBundle
from directory.ingest.evidence import MEDIA_TIMETABLE_SCORE, PageEvidence, TableEvidence


class PromptKind(StrEnum):
    """Which narrow authoring prompt a page set warrants. A str-enum so it stays
    interchangeable with the bare strings the funnel and its tests already use,
    while giving the routing/registry a single typed vocabulary instead of three
    modules hardcoding the same literals."""

    LEGACY = "legacy"  # pre-evidence bundle → the single-shot prompt
    TABLE_CHOICE = "table_choice"  # several tables: pick the timetable, then map it
    TABLE_REPAIR = "table_repair"  # one table: map its columns
    MEDIA = "media"  # image/PDF timetable links
    WIDGET = "widget"  # embedded prayer-time widget
    TERMINAL = "terminal"  # likely no timetable (under construction / wrong site)
    UNKNOWN = "unknown"  # fits no clean category → full schema
    NONE = "none"  # diagnosis-only: deterministic recovery succeeded, no prompt


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
    "widget": {{                      // required for shape "widget"
      "platform": "<provider, e.g. mawaqit | masjidbox>",
      "data_url": "<optional data/API URL>"
    }},
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
- Use "rules" only for fixed times. Use "widget" ONLY for a recognised embedded
  provider (e.g. mawaqit, masjidbox) and you MUST include a "widget" spec with its
  "platform". If you cannot identify the provider, do NOT emit shape "widget" (a
  bare widget is rejected) — return your best html_table/html_repeated/rules.
- Always PREFER a real HTML/structured timetable. Use "image" or "pdf" ONLY when
  the daily timetable in every candidate region is published solely as an image
  (JPG/PNG) or a PDF — common for monthly printable timetables — and no HTML
  table/list of daily times exists anywhere in the candidates. Then set
  "shape":"image" (or "pdf") and "media":{{"url":"<direct image/PDF URL>"}}, and
  still include any "jumuah" block you can read from the HTML. The image/PDF
  itself is read later; never invent a "rules" config to stand in for one.
- Times are 24-hour "HH:MM".
"""


def build_feedback_prompt(base_prompt: str, prev_reply: str, error: str) -> str:
    """Re-prompt after a rejected config: show the model its previous reply and the
    exact reason verification failed, and ask it to fix. The agent has tools, so it
    can WebFetch the live page and confirm its selectors/indices actually select
    times before answering — turning a one-shot guess into a checked correction."""
    return (
        base_prompt
        + "\n\n--- YOUR PREVIOUS ATTEMPT WAS REJECTED ---\n"
        + f"Reason: {error}\n\n"
        + "Your previous reply was:\n"
        + (prev_reply or "")[:2000]
        + "\n\nFix it. You MAY use WebFetch to load the live page and verify that "
        "your selectors/indices actually select the prayer times before answering "
        "(a '0 rows' or 'no occurrences' rejection means they did not). Re-check the "
        "ORIENTATION and shape. Return ONE corrected JSON object and nothing else."
    )


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


# ── type-specific evidence prompts (Phase 5) ──────────────────────────────────
#
# The single-shot prompt asks a cheap model to solve routing, classification and
# full-config authoring in one go. These narrow prompts give it ONE job each over
# compact structured evidence, and ask for the smallest possible answer. The
# parser (author.parse_decision) accepts every envelope they request.

_PRAYER_LINE = f'- "prayer" is exactly one of: {_PRAYERS}.'
_KIND_LINE = '- "kind" is "jamaah" (congregation/iqamah) or "begin" (adhan/start time).'

_TABLE_MAPPING_SCHEMA = f"""\
Return ONE JSON object and nothing else (no prose, no code fences). Prefer the
compact table_mapping; local code builds the full config from it:

{{
  "outcome": "table_mapping",
  "table_id": "<the timetable table's id, e.g. table_0>",
  "orientation": "horizontal_multiday" | "transpose_multiday"
               | "horizontal_single_day" | "prayer_rows",
  "date_index": 0,            // column holding the date (horizontal/transpose multiday)
  "label_index": 0,           // column holding the prayer name (prayer_rows only)
  "columns": [
    {{"kind": "jamaah", "prayer": "fajr", "index": 2, "header_seen": "Fajr Jamaat"}}
  ]
}}

Orientations (read the table first):
- horizontal_multiday: prayer names across the TOP, one DATE per row → set
  "date_index" and one column per prayer by "index".
- transpose_multiday: prayer names down the SIDE, DATES across the top → same
  fields; local code transposes for you (still give post-transpose-free indices
  as shown in the matrix, with the date column's index).
- horizontal_single_day: prayers across the top, a single row of today's times,
  NO date column → omit "date_index".
- prayer_rows: prayers down a left label column, the header names the kind
  (Begin/Iqamah) → set "label_index" and leave each column's "prayer" null.
{_PRAYER_LINE}
{_KIND_LINE}
- For a relative offset jamaah column ("+5"), set "value_kind":"offset" (and an
  optional "base_prayer"); the table must also carry that prayer's begin column.

If a table_mapping cannot capture it, return a full config envelope instead:
{{"outcome":"config","url":"<page url>","config":{{...SourceConfig...}}}}
If the page has NO extractable timetable at all, return:
{{"outcome":"no_timetable","reason":"<why>"}}  (or "wrong_site" if unrelated).
"""

_MEDIA_SCHEMA = """\
Return ONE JSON object and nothing else:
{
  "outcome": "media",
  "kind": "image" | "pdf",
  "url": "<direct URL of the image or PDF timetable>",
  "page_url": "<the page the link is on (optional)>",
  "reason": "<why this is the timetable>"
}
Pick the single link that is the CURRENT monthly/annual prayer timetable. If none
of the links is a timetable, return {"outcome":"no_timetable","reason":"<why>"}.
"""

_WIDGET_SCHEMA = """\
Return ONE JSON object and nothing else:
{
  "outcome": "config",
  "url": "<the widget/page url>",
  "config": {"shape": "widget", "widget": {"platform": "<provider>",
             "data_url": "<optional data/API url>"}}
}
Only name a provider whose widget we can read (e.g. mawaqit). If you cannot
identify a supported provider, return {"outcome":"unknown"}.
"""

_TERMINAL_SCHEMA = """\
Return ONE JSON object and nothing else:
{"outcome": "no_timetable", "reason": "<e.g. site under construction>"}
Use "outcome":"wrong_site" if the site is unrelated to a mosque (a restaurant,
parked domain, etc). If you actually see prayer times, return {"outcome":"unknown"}.
"""


def _render_table_evidence(t: TableEvidence) -> str:
    """A table's evidence as a numbered matrix the model can index into: r0.. rows,
    c0.. columns, plus the selector/caption/named-prayers hints."""
    lines = [f"table_id: {t.table_id}"]
    if t.selector:
        lines.append(f"selector: {t.selector}")
    if t.caption:
        lines.append(f"caption: {t.caption}")
    lines.append(f"header_depth: {t.header_depth}  prayers_named: {t.prayers_named}")
    lines.append(f"date_like_columns: {t.date_like_columns}")
    width = max((len(r) for r in t.matrix), default=0)
    lines.append("      " + "  ".join(f"c{c}" for c in range(width)))
    for r, row in enumerate(t.matrix):
        lines.append(f"  r{r}: " + " | ".join(row))
    return "\n".join(lines)


def _failed_block(failed_attempts: list[tuple[str, str]] | None) -> list[str]:
    if not failed_attempts:
        return []
    out = ["", "Deterministic interpretations already tried, and why each failed:"]
    out.extend(f"- {src}: {reason}" for src, reason in failed_attempts)
    return out


def _tables_block(evidence: list[PageEvidence]) -> list[str]:
    out: list[str] = []
    for page in evidence:
        if not page.tables:
            continue
        out.append(f"\n--- {page.url} ---")
        out.extend(_render_table_evidence(t) for t in page.tables)
    return out


def build_table_repair_prompt(
    evidence: list[PageEvidence], failed_attempts: list[tuple[str, str]] | None = None
) -> str:
    parts = [
        "Map a UK mosque's congregational (jamaah) prayer timetable TABLE into a "
        "column mapping. Tables below are shown as numbered matrices.",
        *_tables_block(evidence),
        *_failed_block(failed_attempts),
        "",
        _TABLE_MAPPING_SCHEMA,
    ]
    return "\n".join(parts)


def build_table_choice_prompt(
    evidence: list[PageEvidence], failed_attempts: list[tuple[str, str]] | None = None
) -> str:
    parts = [
        "A page has several tables. Choose the one that is the prayer timetable and "
        "map it. Tables below are shown as numbered matrices.",
        *_tables_block(evidence),
        *_failed_block(failed_attempts),
        "",
        "Set \"table_id\" to the timetable table.",
        _TABLE_MAPPING_SCHEMA,
    ]
    return "\n".join(parts)


def build_media_prompt(evidence: list[PageEvidence]) -> str:
    parts = ["Identify the mosque's prayer-timetable image/PDF among these links:"]
    for page in evidence:
        for m in page.media_links:
            parts.append(f"- [{m.kind}] {m.url}  (text: {m.text!r}, score {m.score})")
    parts.append("")
    parts.append(_MEDIA_SCHEMA)
    return "\n".join(parts)


def build_widget_prompt(evidence: list[PageEvidence]) -> str:
    parts = ["This site embeds a prayer-time widget. Identify the provider:"]
    for page in evidence:
        for w in page.widget_hints:
            parts.append(f"- provider hint: {w.provider} (data_url: {w.data_url})")
        for ifr in page.iframes:
            parts.append(f"- iframe: {ifr.url} (provider: {ifr.provider_hint})")
    parts.append("")
    parts.append(_WIDGET_SCHEMA)
    return "\n".join(parts)


def build_terminal_classification_prompt(evidence: list[PageEvidence]) -> str:
    parts = ["Does this site publish a mosque prayer timetable, or is it terminal "
             "(under construction / wrong site / empty)?"]
    for page in evidence:
        parts.append(f"\n--- {page.url} (class: {page.page_class}) ---")
        if page.title:
            parts.append(f"title: {page.title}")
        if page.terminal_hints:
            parts.append(f"hints: {page.terminal_hints}")
        parts.append(f"text: {page.visible_text_sample[:400]}")
    parts.append("")
    parts.append(_TERMINAL_SCHEMA)
    return "\n".join(parts)


def build_unknown_prompt(
    bundle: CandidateBundle,
    evidence: list[PageEvidence],
    *,
    max_region_chars: int = 6000,
    max_candidates: int = 5,
) -> str:
    """Last-resort prompt for pages that fit no clean category (no parseable table,
    media or widget). A bare text summary left the model nothing to author selectors
    from, so include the windowed region MARKUP for each candidate page (the real
    DOM — post-render after A1) alongside a compact evidence summary, then the full
    schema. Data first, rules last."""
    region_by_url = {c.url: c.region_html for c in bundle.candidates}
    summary_by_url = {e.url: e for e in evidence}
    parts = [
        "Author a UK mosque's congregational (jamaah) prayer-timetable config from "
        "this site. Each candidate shows a compact evidence summary then a windowed "
        "HTML region centred on the prayer times. If a region is truncated, you MAY "
        "use WebFetch to load the live page before authoring.",
        "",
        "Candidate pages (most likely first):",
    ]
    for c in bundle.candidates[:max_candidates]:
        parts.append(f"\n--- {c.url} ---")
        page = summary_by_url.get(c.url)
        if page is not None:
            parts.append(f"page_class: {page.page_class}")
            if page.media_links:
                parts.append(f"media: {[m.url for m in page.media_links[:3]]}")
            if page.widget_hints:
                parts.append(
                    f"widgets: {[(w.provider, w.data_url) for w in page.widget_hints]}"
                )
        parts.append(_window_region(region_by_url.get(c.url, ""), max_region_chars))
    parts.append("")
    parts.append(_SCHEMA_HINT)
    return "\n".join(parts)


# ── prompt routing + dispatch (Phase 5) ───────────────────────────────────────


def route_prompt_kind(evidence: list[PageEvidence]) -> PromptKind:
    """The narrow prompt kind that fits the strongest evidence on the page set:
    table → media → widget → terminal → unknown. An evidence-less (pre-evidence)
    bundle routes to the legacy single-shot prompt."""
    if not evidence:
        return PromptKind.LEGACY
    if any(len(p.tables) > 1 for p in evidence):
        return PromptKind.TABLE_CHOICE
    if any(p.tables for p in evidence):
        return PromptKind.TABLE_REPAIR
    if any(m.score >= MEDIA_TIMETABLE_SCORE for p in evidence for m in p.media_links):
        return PromptKind.MEDIA
    if any(p.widget_hints for p in evidence):
        return PromptKind.WIDGET
    if any(p.terminal_hints for p in evidence):
        return PromptKind.TERMINAL
    return PromptKind.UNKNOWN


# kind → builder over the evidence. LEGACY and UNKNOWN are handled in build_prompt
# (they need the bundle's windowed region markup, not just the evidence summary).
_PROMPT_BUILDERS = {
    PromptKind.TABLE_CHOICE: lambda evidence, failed: build_table_choice_prompt(evidence, failed),
    PromptKind.TABLE_REPAIR: lambda evidence, failed: build_table_repair_prompt(evidence, failed),
    PromptKind.MEDIA: lambda evidence, failed: build_media_prompt(evidence),
    PromptKind.WIDGET: lambda evidence, failed: build_widget_prompt(evidence),
    PromptKind.TERMINAL: lambda evidence, failed: build_terminal_classification_prompt(evidence),
}


def build_prompt(
    kind: PromptKind,
    bundle: CandidateBundle,
    evidence: list[PageEvidence],
    failed: list[tuple[str, str]],
) -> str:
    """Build the prompt for ``kind``. ``legacy`` (or any evidence-less bundle) falls
    back to the single-shot prompt, so pre-evidence bundles behave exactly as
    before. ``unknown`` carries the windowed region markup alongside the evidence
    summary, so a page that fit no clean category still gives the model real DOM."""
    if kind is PromptKind.LEGACY or not evidence:
        return build_author_prompt(bundle)
    if kind is PromptKind.UNKNOWN:
        return build_unknown_prompt(bundle, evidence)
    return _PROMPT_BUILDERS[kind](evidence, failed)
