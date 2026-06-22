from datetime import date
from pathlib import Path

import pytest

from directory.ingest.extractors.dom_grid import dom_matrix
from directory.ingest.extractors.engine import extract
from directory.ingest.extractors.platforms.dom_grid import DomGridDetector
from directory.ingest.gates import run_gates
from directory.ingest.materialize import materialize

FIXTURES = Path(__file__).parent.parent.parent / "fixtures"
ARIA = (FIXTURES / "dom_grid_aria.html").read_text()
CSS = (FIXTURES / "dom_grid_css.html").read_text()
URL = "https://m.example/prayer-times"

EXPECTED = [
    ["Date", "Fajr", "Dhuhr", "Asr", "Maghrib", "Isha"],
    ["1 Jun", "03:15", "13:30", "18:45", "21:30", "23:00"],
    ["2 Jun", "03:16", "13:30", "18:46", "21:31", "23:01"],
    ["3 Jun", "03:17", "13:30", "18:47", "21:32", "23:02"],
]


@pytest.mark.parametrize("html", [ARIA, CSS])
def test_dom_matrix_reconstructs_grid(html):
    assert dom_matrix(html) == EXPECTED


def test_dom_matrix_none_on_plain_page():
    assert dom_matrix("<html><body><p>No grid here at all.</p></body></html>") is None


@pytest.mark.parametrize("html", [ARIA, CSS])
def test_detects_and_sets_dom_grid_flag(html):
    match = DomGridDetector().detect(html, URL)
    assert match is not None
    assert match.platform == "dom_grid"
    assert match.requires_js is True
    assert match.config.shape == "html_table"
    assert match.config.grid.dom_grid is True


@pytest.mark.parametrize("html", [ARIA, CSS])
def test_end_to_end_through_engine(html):
    # The synthesised matrix flows through the unchanged table engine + gates.
    match = DomGridDetector().detect(html, URL)
    cfg = match.config
    today = date(2026, 6, 1)
    res = extract(html, cfg, year=2026, month=6, today=today)
    rows = materialize(res, cfg, horizon_start=today, horizon_end=date(2026, 6, 30))
    by = {(r.date, r.prayer): r.jamaah_time for r in rows}
    assert by[("2026-06-01", "fajr")] == "03:15"
    assert by[("2026-06-03", "isha")] == "23:02"
    gate = run_gates(cfg, res, rows, html_text=html)
    assert gate.lane == "auto_accept"
