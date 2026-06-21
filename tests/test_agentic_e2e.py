import json
from datetime import date

from directory import repository as repo
from directory.db import session_scope
from directory.ingest.author import author_mosque
from directory.ingest.bespoke_store import load_bespoke
from directory.ingest.candidate_store import save_bundle
from directory.ingest.discover import Candidate, CandidateBundle
from directory.ingest.extractors.bespoke import BESPOKE_EXTRACTORS
from directory.ingest.fetch import FetchResult
from directory.ingest.runner import extract_source
from directory.models import Mosque, Source
from tests.conftest import FakeBrowsingHarness, FakeHarness
from tests.test_author_fallback import ACME_HTML, ACME_MODULE

BESPOKE_OUTPUT = json.dumps({
    "url": "https://e2e.example/custom",
    "config": {"shape": "bespoke", "bespoke": {"module": "acme_e2e"}},
    "module_code": ACME_MODULE.replace('"acme"', '"acme_e2e"'),
})


def _acme_fetcher(url, **kwargs):
    return FetchResult(url, 200, ACME_HTML, "h", error=None)


def test_candidate_to_agentic_bespoke_to_daily_reload(engine, tmp_path):
    bespoke_root = tmp_path / "bespoke"
    with session_scope(engine) as s:
        s.add(Mosque(id="e2e", name="E2E", lat=52.0, lng=-1.0,
                     website_url="https://e2e.example/"))
        s.add(Source(id="e2e", mosque_id="e2e", url="https://e2e.example/custom",
                     triage_status="candidate"))
    save_bundle(
        CandidateBundle("e2e", "https://e2e.example/",
                        [Candidate("https://e2e.example/custom", 9.0, ACME_HTML, "Fajr")]),
        root=tmp_path,
    )

    # Single-shot fails; the agentic fallback authors a bespoke module.
    out = author_mosque(
        engine, "e2e", harness=FakeHarness("cannot map this layout"), candidate_root=tmp_path,
        models=("cheap", "strong"), fallback=FakeBrowsingHarness(BESPOKE_OUTPUT),
        bespoke_root=bespoke_root, today=date(2026, 6, 1), horizon_days=5, fetcher=_acme_fetcher,
    )
    assert out.outcome == "authored"

    # Simulate a fresh process for the daily cron: drop the in-memory registry,
    # reload from disk, then extract the now-authored source again.
    BESPOKE_EXTRACTORS.pop("acme_e2e", None)
    assert "acme_e2e" not in BESPOKE_EXTRACTORS
    load_bespoke(bespoke_root)

    result = extract_source(
        engine, "e2e", today=date(2026, 6, 2), horizon_days=5, fetcher=_acme_fetcher,
    )
    assert result.triage_status == "authored"
    assert result.rows_written > 0
    with session_scope(engine) as s:
        assert repo.get_source(s, "e2e").shape == "bespoke"
