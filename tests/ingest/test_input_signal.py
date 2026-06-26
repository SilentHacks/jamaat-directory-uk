"""The model-input signal that splits 'blinded' from 'misread' (measurement loop)."""

from datetime import date

from directory.ingest.discover import Candidate, CandidateBundle
from directory.ingest.evidence import build_page_evidence
from directory.ingest.input_signal import model_input_signal

TODAY = date(2026, 6, 1)

TABLE = (
    "<table><tr><th>Date</th><th>Fajr</th><th>Dhuhr</th><th>Asr</th><th>Maghrib</th>"
    "<th>Isha</th></tr>"
    "<tr><td>1 June</td><td>05:00</td><td>13:30</td><td>18:30</td><td>21:30</td>"
    "<td>23:00</td></tr></table>"
)
SHELL = '<html><body><div id="__next"></div><h1>Prayer Times</h1></body></html>'


def _bundle(region_html, evidence_html=None):
    cand = Candidate(url="https://m.example/p", score=5.0, region_html=region_html, text="t")
    evidence = (
        [build_page_evidence(evidence_html, "https://m.example/p", today=TODAY)]
        if evidence_html is not None
        else []
    )
    return CandidateBundle("m", "https://m.example/", [cand], evidence=evidence)


def test_table_region_has_times():
    sig = model_input_signal(_bundle(TABLE, TABLE))
    assert sig.time_count > 0
    assert sig.distinct_prayers >= 2
    assert sig.has_times is True


def test_empty_shell_has_no_times():
    # The newhammosques / abraracademy failure shape: the model is handed a shell
    # with prayer wording but no actual times.
    sig = model_input_signal(_bundle(SHELL, SHELL))
    assert sig.time_count == 0
    assert sig.table_time_count == 0
    assert sig.has_times is False


def test_widget_evidence_counts_as_authorable_input():
    html = (
        '<html><body><a href="https://time.my-masjid.com/timingscreen/'
        'f4c8cc40-8e42-47ce-9e74-d8125a10b0ba">Prayer Times</a></body></html>'
    )
    sig = model_input_signal(_bundle("<html>chrome</html>", html))
    assert sig.has_widget is True
    assert sig.has_times is True  # a widget is authorable even with no raw times shown


def test_media_evidence_counts_as_authorable_input():
    html = '<html><body><a href="/june-2026-prayer-timetable.pdf">June</a></body></html>'
    sig = model_input_signal(_bundle("<html>chrome</html>", html))
    assert sig.has_media is True
    assert sig.has_times is True


def test_window_surfaces_buried_times_like_the_prompt():
    # The signal uses the same windowing as the prompt, which centres on the densest
    # cluster of clock times — so times buried after a long chrome prefix are still
    # counted (the prompt would show them too).
    region = ("x" * 9000) + "<td>05:00</td><td>13:30</td>"
    sig = model_input_signal(_bundle(region), max_region_chars=2000)
    assert sig.time_count == 2
