from directory.ingest.discover import Candidate, CandidateBundle
from directory.ingest.prompt import build_author_prompt, build_browse_prompt


def _bundle():
    return CandidateBundle(
        mosque_id="m1",
        base_url="https://m1.example/",
        candidates=[
            Candidate(
                url="https://m1.example/prayer-times",
                score=9.0,
                region_html="<table><tr><th>Fajr</th></tr></table>",
                text="Fajr 05:00",
            ),
            Candidate(url="https://m1.example/x", score=1.0, region_html="<p>x</p>", text="x"),
        ],
    )


def test_prompt_includes_site_and_candidate_urls():
    p = build_author_prompt(_bundle())
    assert "https://m1.example/" in p
    assert "https://m1.example/prayer-times" in p


def test_prompt_lists_the_required_output_shape_and_vocab():
    p = build_author_prompt(_bundle())
    assert '"url"' in p and '"config"' in p
    assert "html_table" in p and "html_repeated" in p
    # every Prayer enum value must be advertised to the model
    for name in ("fajr", "dhuhr", "asr", "maghrib", "isha", "jumuah"):
        assert name in p


def test_prompt_documents_relative_offset_columns():
    p = build_author_prompt(_bundle())
    assert "value_kind" in p
    assert "offset" in p
    assert "base_prayer" in p


def test_prompt_documents_vertical_and_single_day_layouts():
    p = build_author_prompt(_bundle())
    assert "prayer_label_index" in p
    assert "single_day" in p


def test_author_prompt_documents_url_template_paging():
    p = build_author_prompt(_bundle())
    assert "paging" in p
    assert "url_template" in p
    assert "{month" in p  # placeholder advertised to the model


def test_browse_prompt_documents_both_paging_modes():
    p = build_browse_prompt(_bundle())
    assert "url_template" in p
    assert "render_nav" in p
    assert "next_selector" in p and "month_select" in p


def test_author_prompt_documents_packed_cell_time_index():
    p = build_author_prompt(_bundle())
    assert "time_index" in p
    # and steers away from the (unsupported for html_table) intra-cell selector
    low = p.lower()
    assert "same" in low and "index" in low and "iqamah" in low


def test_author_prompt_explains_table_orientation():
    p = build_author_prompt(_bundle())
    low = p.lower()
    # the model must first read orientation: prayer names across the top (columns)
    # vs down a left label column (rows)
    assert "orientation" in low
    assert "label column" in low


def test_author_prompt_transpose_covers_prayers_as_columns():
    p = build_author_prompt(_bundle())
    low = p.lower()
    # transpose is for prayer names running across the top, not only when DATES
    # are the columns — e.g. a daily widget with Begins/Jamaah as separate rows
    assert "transpose" in low
    assert "separate row" in low


def test_author_prompt_time_index_only_for_one_packed_cell():
    p = build_author_prompt(_bundle())
    low = p.lower()
    # time_index applies ONLY to a single cell holding two clock times; begin and
    # jamaah in separate rows/columns is a structural (transpose/label) case
    assert "time_index" in p
    assert "two clock times" in low
    assert "separate" in low


def test_browse_prompt_documents_packed_cell_time_index():
    p = build_browse_prompt(_bundle())
    assert "time_index" in p


def test_browse_prompt_explains_table_orientation():
    p = build_browse_prompt(_bundle())
    assert "orientation" in p.lower()


def test_author_prompt_documents_image_pdf_fallback():
    p = build_author_prompt(_bundle())
    # image/pdf are offered as last-resort shapes with a media URL...
    assert '"image"' in p and '"pdf"' in p
    assert "media" in p
    # ...and the model is told to prefer a real HTML timetable first.
    assert "image" in p.lower() and "html" in p.lower()


def test_browse_prompt_documents_image_pdf_and_prefers_html():
    p = build_browse_prompt(_bundle())
    assert '"image"' in p and '"pdf"' in p
    assert "media" in p
    # The agent must keep looking for an HTML timetable before falling back.
    low = p.lower()
    assert "keep" in low or "only" in low  # instruction to exhaust HTML first


def test_prompt_truncates_regions_and_caps_candidate_count():
    big = Candidate(url="https://m1.example/big", score=5.0, region_html="x" * 9000, text="t")
    bundle = CandidateBundle("m1", "https://m1.example/", [big, big, big, big])
    p = build_author_prompt(bundle, max_region_chars=100, max_candidates=2)
    assert "x" * 101 not in p          # region truncated
    assert p.count("candidate 3:") == 0  # only 2 candidates rendered


# ── type-specific evidence prompts (Phase 5) ──────────────────────────────────

from datetime import date  # noqa: E402

from directory.ingest.evidence import build_page_evidence  # noqa: E402
from directory.ingest.prompt import (  # noqa: E402
    build_media_prompt,
    build_table_choice_prompt,
    build_table_repair_prompt,
    build_terminal_classification_prompt,
    build_unknown_prompt,
    build_widget_prompt,
)

_TODAY = date(2026, 6, 1)

MONTHLY = (
    "<table class='pt'><tr><th>Date</th><th>Fajr</th><th>Dhuhr</th><th>Asr</th>"
    "<th>Maghrib</th><th>Isha</th></tr>"
    "<tr><td>1 June</td><td>05:00</td><td>13:30</td><td>18:30</td><td>21:30</td><td>23:00</td></tr>"
    "</table>"
)
PDFP = '<html><body><a href="/june-2026-prayer-timetable.pdf">June Timetable</a></body></html>'
UC = "<html><body><h1>Site under construction — coming soon.</h1></body></html>"


def _ev(html):
    return [build_page_evidence(html, "https://m.example/p", today=_TODAY)]


def test_table_repair_prompt_numbers_rows_and_columns():
    p = build_table_repair_prompt(_ev(MONTHLY))
    assert "table_mapping" in p
    assert "r0:" in p and "c0" in p          # numbered matrix
    assert "orientation" in p
    # vocab + kinds advertised
    for name in ("fajr", "dhuhr", "asr", "maghrib", "isha"):
        assert name in p


def test_table_repair_prompt_includes_failed_reasons():
    p = build_table_repair_prompt(
        _ev(MONTHLY), [("enumerator:table_horizontal_multiday", "no occurrences produced")]
    )
    assert "already tried" in p
    assert "no occurrences produced" in p


def test_table_choice_prompt_asks_to_pick_a_table():
    p = build_table_choice_prompt(_ev(MONTHLY))
    assert "table_id" in p


def test_media_prompt_lists_links_and_omits_full_schema():
    p = build_media_prompt(_ev(PDFP))
    assert "june-2026-prayer-timetable.pdf" in p
    assert '"outcome": "media"' in p
    # the media prompt must NOT dump the whole grid schema at the model
    assert "table_selector" not in p
    assert "prayer_label_index" not in p


def test_terminal_prompt_allows_no_timetable():
    p = build_terminal_classification_prompt(_ev(UC))
    assert "no_timetable" in p
    assert "wrong_site" in p
    assert "under construction" in p.lower()


def test_unknown_prompt_includes_region_markup_and_full_schema():
    # A page that fits no clean category must still hand the model the real DOM
    # (the windowed region), not just a text summary, plus the full schema.
    region = "<div class='times'>Fajr 05:00 Dhuhr 13:30</div>"
    bundle = CandidateBundle(
        "m1", "https://m.example/",
        [Candidate(url="https://m.example/p", score=5.0, region_html=region, text="t")],
    )
    p = build_unknown_prompt(bundle, _ev(MONTHLY))
    assert "html_table" in p and "html_repeated" in p   # full schema present
    assert "class='times'" in p                          # real markup present
    assert "page_class:" in p                            # evidence summary present


def test_widget_prompt_asks_for_provider():
    iframe = '<html><body><iframe src="https://mawaqit.net/en/x"></iframe></body></html>'
    p = build_widget_prompt(_ev(iframe))
    assert "platform" in p
    assert "mawaqit" in p
