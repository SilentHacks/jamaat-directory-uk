from datetime import date

from directory.ingest.discover import Candidate, CandidateBundle
from directory.ingest.evidence import (
    MEDIA_TIMETABLE_SCORE,
    build_page_evidence,
    classify_page,
    terminal_no_timetable,
)

TODAY = date(2026, 6, 1)

MONTHLY_TABLE = """
<html><body><h1>Prayer Timetable</h1>
<table id="pt">
  <tr><th>Date</th><th>Fajr</th><th>Dhuhr</th><th>Asr</th><th>Maghrib</th><th>Isha</th></tr>
  <tr><td>1 June</td><td>05:00</td><td>13:15</td><td>18:30</td><td>21:10</td><td>22:30</td></tr>
  <tr><td>2 June</td><td>05:02</td><td>13:16</td><td>18:31</td><td>21:11</td><td>22:31</td></tr>
  <tr><td>3 June</td><td>05:03</td><td>13:17</td><td>18:32</td><td>21:12</td><td>22:32</td></tr>
</table></body></html>
"""

UNDER_CONSTRUCTION = (
    "<html><head><title>Welcome</title></head><body>"
    "<h1>Our website is under construction. Coming soon!</h1></body></html>"
)
RESTAURANT = (
    "<html><body><h1>Spice Garden Restaurant</h1>"
    "<p>View our menu and book a table for a great dining experience. "
    "Order online for free delivery.</p></body></html>"
)
EMPTY = "<html><head></head><body></body></html>"
SPAM = (
    "<html><body><h1>This domain is for sale</h1>"
    "<p>Buy this domain. Best online casino and sports betting offers.</p></body></html>"
)


# ── table extraction ──────────────────────────────────────────────────────────


def test_table_extraction_captures_prayers_times_and_date_column():
    ev = build_page_evidence(MONTHLY_TABLE, "https://m.example/pt", today=TODAY)
    assert ev.page_class == "structured_html"
    assert len(ev.tables) == 1
    t = ev.tables[0]
    assert t.table_id == "table_0"
    assert t.selector == "table#pt"
    assert set(t.prayers_named) == {"fajr", "dhuhr", "asr", "maghrib", "isha"}
    assert t.time_count == 15  # 3 body rows × 5 prayer columns
    assert 0 in t.date_like_columns
    assert t.header[:1] == ["Date"]


def test_table_matrix_and_body_sample_are_capped():
    rows = "".join(
        f"<tr><td>{i} June</td><td>05:0{i % 10}</td></tr>" for i in range(1, 30)
    )
    html = f"<html><body><table><tr><th>Date</th><th>Fajr</th></tr>{rows}</table></body></html>"
    ev = build_page_evidence(html, "https://m.example/", today=TODAY)
    t = ev.tables[0]
    assert len(t.matrix) <= 16
    assert len(t.body_sample) <= 6


# ── media extraction ──────────────────────────────────────────────────────────


def test_media_pdf_link_scored_as_timetable():
    html = (
        '<html><body><a href="/files/prayer-timetable-june-2026.pdf">'
        "June Timetable</a></body></html>"
    )
    ev = build_page_evidence(html, "https://m.example/", today=TODAY)
    assert len(ev.media_links) == 1
    m = ev.media_links[0]
    assert m.kind == "pdf"
    assert m.url == "https://m.example/files/prayer-timetable-june-2026.pdf"
    assert m.score >= MEDIA_TIMETABLE_SCORE
    assert ev.page_class == "media_only"


def test_media_image_link_resolved_absolute():
    html = '<html><body><img src="june.jpg" alt="prayer timetable"></body></html>'
    ev = build_page_evidence(html, "https://m.example/sub/", today=TODAY)
    assert len(ev.media_links) == 1
    assert ev.media_links[0].kind == "image"
    assert ev.media_links[0].url == "https://m.example/sub/june.jpg"


def test_non_timetable_image_scores_low():
    html = '<html><body><p>Welcome</p><img src="/logo.png" alt="logo"></body></html>'
    ev = build_page_evidence(html, "https://m.example/", today=TODAY)
    assert ev.media_links and ev.media_links[0].score < MEDIA_TIMETABLE_SCORE


def test_cms_download_pattern_treated_as_pdf():
    html = '<html><body><a href="/?wpdmdl=123">Download timetable</a></body></html>'
    ev = build_page_evidence(html, "https://m.example/", today=TODAY)
    assert ev.media_links and ev.media_links[0].kind == "pdf"


# ── iframe / widget extraction ────────────────────────────────────────────────


def test_iframe_provider_detected_and_widget_hint_emitted():
    html = (
        '<html><body><iframe src="https://mawaqit.net/en/w/my-masjid" '
        'title="Prayer widget"></iframe></body></html>'
    )
    ev = build_page_evidence(html, "https://m.example/", today=TODAY)
    assert len(ev.iframes) == 1
    assert ev.iframes[0].provider_hint == "mawaqit"
    assert any(w.provider == "mawaqit" and w.confidence >= 0.9 for w in ev.widget_hints)
    assert ev.page_class == "iframe_or_widget"


def test_masjidbox_marker_without_iframe_is_low_confidence_hint():
    html = '<html><body><script src="https://masjidbox.com/embed.js"></script></body></html>'
    ev = build_page_evidence(html, "https://m.example/", today=TODAY)
    assert any(w.provider == "masjidbox" for w in ev.widget_hints)


def test_anchor_button_to_my_masjid_screen_becomes_widget_hint_with_api():
    # A mosque page that links to its my-masjid timing screen with a button (no
    # iframe): A2 must follow the anchor, and the hint's data_url must be the JSON
    # API (not the Angular shell URL) so the enumerator can verify it for £0.
    guid = "f4c8cc40-8e42-47ce-9e74-d8125a10b0ba"
    html = (
        f'<html><body><h1>Welcome</h1>'
        f'<a href="https://time.my-masjid.com/timingscreen/{guid}">Prayer Times</a>'
        f'</body></html>'
    )
    ev = build_page_evidence(html, "https://alhuda.example/", today=TODAY)
    hint = next(w for w in ev.widget_hints if w.provider == "mylocalmasjid")
    assert hint.data_url == (
        "https://time.my-masjid.com/api/TimingsInfoScreen/GetMasjidTimings"
        f"?GuidId={guid}"
    )
    assert ev.page_class == "iframe_or_widget"  # not terminal, not empty


# ── JS shell ──────────────────────────────────────────────────────────────────


def test_js_shell_empty_prayer_table_flagged():
    html = (
        "<html><body><table><tr><th>Fajr</th><th>Dhuhr</th><th>Asr</th>"
        "<th>Maghrib</th><th>Isha</th></tr></table>"
        '<div data-reactroot></div></body></html>'
    )
    ev = build_page_evidence(html, "https://m.example/prayer-times", today=TODAY)
    assert "empty_prayer_table" in ev.js_hints
    assert ev.page_class == "js_shell"


# ── terminal classification ───────────────────────────────────────────────────


def test_classify_under_construction():
    assert classify_page(UNDER_CONSTRUCTION, "https://m.example/", today=TODAY) == (
        "under_construction"
    )


def test_classify_restaurant_is_irrelevant():
    assert classify_page(RESTAURANT, "https://m.example/", today=TODAY) == "irrelevant"


def test_classify_empty_page():
    assert classify_page(EMPTY, "https://m.example/", today=TODAY) == "empty"


def test_classify_spam_or_parked():
    assert classify_page(SPAM, "https://m.example/", today=TODAY) == "parked_or_spam"


def test_classify_structured_table():
    assert classify_page(MONTHLY_TABLE, "https://m.example/", today=TODAY) == (
        "structured_html"
    )


def test_keyword_links_keep_a_bare_page_ambiguous():
    # A homepage that only links to a prayer-times page (timetable lives elsewhere)
    # must never be terminal — the linked page may simply be unreachable right now.
    html = '<html><body><a href="/prayer-times">Prayer Times</a></body></html>'
    assert classify_page(html, "https://m.example/", today=TODAY) == "unknown"


# ── terminal routing ──────────────────────────────────────────────────────────


def test_terminal_no_timetable_fires_when_all_pages_terminal():
    evs = [
        build_page_evidence(UNDER_CONSTRUCTION, "https://m.example/", today=TODAY),
        build_page_evidence(EMPTY, "https://m.example/about", today=TODAY),
    ]
    result = terminal_no_timetable(evs)
    assert result is not None
    last_status, last_error = result
    assert last_status == "under_construction"  # most specific wins the tie-break
    assert "construction" in last_error


def test_terminal_no_timetable_aborts_when_any_page_has_evidence():
    evs = [
        build_page_evidence(UNDER_CONSTRUCTION, "https://m.example/", today=TODAY),
        build_page_evidence(MONTHLY_TABLE, "https://m.example/pt", today=TODAY),
    ]
    assert terminal_no_timetable(evs) is None


def test_terminal_no_timetable_aborts_on_media_evidence():
    media = (
        '<html><body><a href="/prayer-timetable-2026.pdf">June</a></body></html>'
    )
    evs = [build_page_evidence(media, "https://m.example/", today=TODAY)]
    assert terminal_no_timetable(evs) is None


def test_terminal_no_timetable_none_for_empty_list():
    assert terminal_no_timetable([]) is None


def test_near_empty_page_with_opaque_media_is_not_terminal():
    # A near-empty body whose only content is an opaquely named image (score 0)
    # may itself be a timetable — the classifier routes it to media_only, so the
    # terminal verdict aborts (never hide a real timetable).
    html = '<html><body><img src="/uploads/pt.jpg"></body></html>'
    ev = build_page_evidence(html, "https://m.example/", today=TODAY)
    assert ev.media_links and ev.media_links[0].score < MEDIA_TIMETABLE_SCORE
    assert ev.page_class == "media_only"
    assert terminal_no_timetable([ev]) is None


def test_opaque_pdf_aborts_terminal_verdict_even_when_low_scoring():
    # A PDF is almost never page chrome; even unscored it must block a terminal
    # verdict on an otherwise-terminal page.
    page = '<html><body><h1>Spice Garden Restaurant — our menu</h1>'
    page += '<a href="/x.pdf">download</a></body></html>'
    ev = build_page_evidence(page, "https://m.example/", today=TODAY)
    assert ev.media_links[0].kind == "pdf"
    assert ev.media_links[0].score < MEDIA_TIMETABLE_SCORE
    assert terminal_no_timetable([ev]) is None


def test_near_empty_spa_shell_is_js_shell_not_empty():
    # A generic SPA shell (framework not in the named marker list) with almost no
    # static text must be rescued from a terminal "empty" verdict.
    html = '<html><body><div id="app"></div><script src="/app.js"></script></body></html>'
    ev = build_page_evidence(html, "https://m.example/", today=TODAY)
    assert ev.page_class == "js_shell"
    assert terminal_no_timetable([ev]) is None


def test_genuinely_empty_body_stays_empty():
    ev = build_page_evidence(EMPTY, "https://m.example/", today=TODAY)
    assert ev.page_class == "empty"
    assert terminal_no_timetable([ev]) is not None


def test_wrong_site_maps_to_wrong_site_last_status():
    evs = [build_page_evidence(RESTAURANT, "https://m.example/", today=TODAY)]
    result = terminal_no_timetable(evs)
    assert result is not None and result[0] == "wrong_site"


# ── JSON roundtrip / backward compatibility ───────────────────────────────────


def test_bundle_roundtrips_with_evidence(tmp_path):
    ev = build_page_evidence(MONTHLY_TABLE, "https://m.example/pt", today=TODAY)
    bundle = CandidateBundle(
        mosque_id="m1",
        base_url="https://m.example/",
        candidates=[Candidate("https://m.example/pt", 9.0, "<table/>", "05:00")],
        evidence=[ev],
    )
    bundle.save(tmp_path)
    loaded = CandidateBundle.load("m1", tmp_path)
    assert len(loaded.evidence) == 1
    t = loaded.evidence[0]
    assert t.page_class == "structured_html"
    assert t.tables[0].prayers_named == ev.tables[0].prayers_named
    assert t.tables[0].selector == "table#pt"


def test_bundle_load_backward_compatible_without_evidence(tmp_path):
    # A bundle written before the evidence field had no "evidence" key.
    (tmp_path / "old.json").write_text(
        '{"mosque_id": "old", "base_url": "https://m.example/", '
        '"candidates": [{"url": "https://m.example/t", "score": 1.0, '
        '"region_html": "<table/>", "text": "x"}]}',
        encoding="utf-8",
    )
    loaded = CandidateBundle.load("old", tmp_path)
    assert loaded is not None
    assert loaded.evidence == []
    assert loaded.candidates[0].url == "https://m.example/t"
