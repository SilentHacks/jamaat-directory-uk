from directory.ingest.discover import Candidate, CandidateBundle


def test_roundtrip(tmp_path):
    bundle = CandidateBundle(
        mosque_id="m1",
        base_url="https://m.example/",
        candidates=[
            Candidate(
                url="https://m.example/t", score=9.0, region_html="<table/>", text="05:00"
            )
        ],
    )
    path = bundle.save(tmp_path)
    assert path.exists()

    loaded = CandidateBundle.load("m1", tmp_path)
    assert loaded.mosque_id == "m1"
    assert loaded.candidates[0].score == 9.0
    assert loaded.candidates[0].url == "https://m.example/t"


def test_missing_returns_none(tmp_path):
    assert CandidateBundle.load("nope", tmp_path) is None
