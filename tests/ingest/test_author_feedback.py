# tests/ingest/test_author_feedback.py
import json
from datetime import date

from directory.db import session_scope
from directory.ingest.author import author_mosque
from directory.ingest.discover import Candidate, CandidateBundle
from directory.ingest.fetch import FetchResult
from directory.ingest.harness import HarnessResult
from directory.models import Mosque, Source

TABLE_HTML = (
    "<table class='t'><tr><th>Date</th><th>Fajr</th><th>Dhuhr</th><th>Asr</th>"
    "<th>Maghrib</th><th>Isha</th></tr>"
    "<tr><td>1 June</td><td>05:00</td><td>13:30</td><td>18:30</td><td>21:30</td><td>23:00</td></tr>"
    "<tr><td>2 June</td><td>05:02</td><td>13:31</td><td>18:31</td><td>21:31</td><td>23:01</td></tr>"
    "</table>"
)


def _good_output(url):
    return json.dumps({
        "url": url,
        "config": {"shape": "html_table", "grid": {
            "table_selector": "table.t", "date": {"index": 0}, "columns": [
                {"kind": "jamaah", "prayer": "fajr", "index": 1},
                {"kind": "jamaah", "prayer": "dhuhr", "index": 2},
                {"kind": "jamaah", "prayer": "asr", "index": 3},
                {"kind": "jamaah", "prayer": "maghrib", "index": 4},
                {"kind": "jamaah", "prayer": "isha", "index": 5}]}},
    })


def _candidate(engine, mid, root):
    url = f"https://{mid}.example/prayer-times"
    with session_scope(engine) as s:
        s.add(Mosque(id=mid, name=mid, city="X", lat=52.0, lng=-1.0,
                     website_url=f"https://{mid}.example/"))
        s.add(Source(id=mid, mosque_id=mid, url=url, triage_status="candidate"))
    CandidateBundle(mid, f"https://{mid}.example/",
                    [Candidate(url, 9.0, TABLE_HTML, "Fajr")]).save(root)
    return url


def _fetcher(url, **kwargs):
    return FetchResult(url, 200, TABLE_HTML, "h", error=None)


class SeqHarness:
    """Returns scripted replies in order (repeating the last), recording prompts."""

    name = "seq"

    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts: list[str] = []

    def run(self, prompt, *, model):
        self.prompts.append(prompt)
        text = self.replies[min(len(self.prompts) - 1, len(self.replies) - 1)]
        return HarnessResult(text, model, True)


def test_feedback_retry_recovers_a_rejected_config(engine, tmp_path):
    url = _candidate(engine, "m1", tmp_path)
    harness = SeqHarness(["not json at all", _good_output(url)])  # bad, then good

    out = author_mosque(
        engine, "m1", harness=harness, candidate_root=tmp_path, models=("opus@low",),
        today=date(2026, 6, 1), horizon_days=5, fetcher=_fetcher, feedback_retries=1,
    )

    assert out.outcome == "authored"
    assert len(harness.prompts) == 2  # one corrective re-prompt
    assert "PREVIOUS ATTEMPT WAS REJECTED" in harness.prompts[1]
    assert "not json at all" in harness.prompts[1]  # prior reply fed back


def test_no_feedback_retry_when_disabled(engine, tmp_path):
    _candidate(engine, "m2", tmp_path)
    harness = SeqHarness(["not json at all", _good_output("https://m2.example/prayer-times")])

    out = author_mosque(
        engine, "m2", harness=harness, candidate_root=tmp_path, models=("opus@low",),
        today=date(2026, 6, 1), horizon_days=5, fetcher=_fetcher, feedback_retries=0,
    )

    assert out.outcome == "needs_reauthor"
    assert len(harness.prompts) == 1  # no second attempt
