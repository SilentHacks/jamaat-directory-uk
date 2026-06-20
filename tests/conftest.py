import pytest

from directory.db import init_db, make_engine, session_scope
from directory.ingest.harness import HarnessResult
from directory.models import Mosque, Occurrence


@pytest.fixture
def engine(tmp_path):
    eng = make_engine(f"sqlite:///{tmp_path/'t.db'}")
    init_db(eng)
    return eng


@pytest.fixture
def seeded(engine):
    with session_scope(engine) as s:
        s.add_all(
            [
                Mosque(id="leic", name="Leicester Masjid", city="Leicester",
                       lat=52.6225, lng=-1.1106, website_url="https://a.example"),
                Mosque(id="lon", name="London Masjid", city="London",
                       lat=51.5074, lng=-0.1278, website_url=None),
            ]
        )
    with session_scope(engine) as s:
        s.add_all(
            [
                Occurrence(mosque_id="leic", date="2026-06-21", prayer="fajr",
                           session_idx=0, jamaah_time="05:00", begin_time="04:45"),
                Occurrence(mosque_id="leic", date="2026-06-21", prayer="jumuah",
                           session_idx=1, jamaah_time="13:00", label="1st Jumu'ah"),
                Occurrence(mosque_id="leic", date="2026-06-21", prayer="jumuah",
                           session_idx=2, jamaah_time="13:45", label="2nd Jumu'ah"),
            ]
        )
    return engine


class FakeHarness:
    """Deterministic AuthorHarness double. `script` is a {model: text} dict or a
    plain string returned for every model. Records calls in `.calls`."""

    name = "fake"

    def __init__(self, script):
        self.script = script
        self.calls: list[str] = []

    def run(self, prompt: str, *, model: str) -> HarnessResult:
        self.calls.append(model)
        text = self.script.get(model, "") if isinstance(self.script, dict) else self.script
        return HarnessResult(text, model, True)
