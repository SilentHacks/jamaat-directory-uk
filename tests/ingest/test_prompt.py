from directory.ingest.discover import Candidate, CandidateBundle
from directory.ingest.prompt import build_author_prompt


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


def test_prompt_truncates_regions_and_caps_candidate_count():
    big = Candidate(url="https://m1.example/big", score=5.0, region_html="x" * 9000, text="t")
    bundle = CandidateBundle("m1", "https://m1.example/", [big, big, big, big])
    p = build_author_prompt(bundle, max_region_chars=100, max_candidates=2)
    assert "x" * 101 not in p          # region truncated
    assert p.count("candidate 3:") == 0  # only 2 candidates rendered
