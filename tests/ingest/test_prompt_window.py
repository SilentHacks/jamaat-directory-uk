# tests/ingest/test_prompt_window.py
from directory.ingest.discover import Candidate, CandidateBundle
from directory.ingest.prompt import _window_region, build_author_prompt


def test_window_region_anchors_on_clock_cluster():
    """A budget-sized window centres on the densest run of clock times, not the
    leading bytes — so a table buried past the budget is still surfaced."""
    pad = "x" * 12000
    table = "Fajr 05:00 Dhuhr 13:30 Asr 18:30 Maghrib 21:30 Isha 23:00"
    html = pad + table + ("y" * 3000)

    out = _window_region(html, 4000)

    assert "05:00" in out and "23:00" in out  # the deep table made it in
    assert len(out) <= 4000


def test_window_region_short_html_returned_whole():
    assert _window_region("Fajr 05:00", 4000) == "Fajr 05:00"


def test_window_region_no_clocks_falls_back_to_head():
    html = "z" * 9000
    out = _window_region(html, 4000)
    assert out == html[:4000]


def test_build_author_prompt_surfaces_deep_table_and_more_candidates():
    deep = ("x" * 12000) + "Fajr 05:00 Dhuhr 13:30 Asr 18:30 Maghrib 21:30 Isha 23:00"
    cands = [
        Candidate(f"https://m.example/p{i}", float(9 - i), deep, "Fajr") for i in range(5)
    ]
    bundle = CandidateBundle("m", "https://m.example/", cands)

    prompt = build_author_prompt(bundle)

    assert "05:00" in prompt  # deep table surfaced despite the 12k preamble
    assert "candidate 5" in prompt  # up to 5 candidates embedded (was 3)
