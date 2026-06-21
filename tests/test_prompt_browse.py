from directory.ingest.discover import Candidate, CandidateBundle
from directory.ingest.prompt import build_browse_prompt


def _bundle():
    return CandidateBundle(
        "m1", "https://m1.example/",
        [Candidate("https://m1.example/prayer-times", 9.0, "<table/>", "Fajr")],
    )


def test_browse_prompt_includes_site_and_bespoke_envelope():
    prompt = build_browse_prompt(_bundle())

    assert "https://m1.example/" in prompt
    assert "bespoke" in prompt
    assert "module_code" in prompt
    assert "register_bespoke" in prompt
    # Every prayer token the agent may emit is documented.
    for token in ("fajr", "dhuhr", "asr", "maghrib", "isha", "jumuah"):
        assert token in prompt
