import pytest

from directory import repository as repo
from directory.db import session_scope
from directory.ingest.seed import load_seed_file, seed_database
from tests.conftest import FIXTURES

FIXTURE = FIXTURES / "seed_sample.json"


def test_load_seed_file_parses_records():
    records = load_seed_file(FIXTURE)
    assert len(records) == 2
    assert records[1]["website_url"] is None


def test_load_seed_file_rejects_missing_required(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text('[{"id": "x", "name": "Y"}]')  # no lat/lng
    with pytest.raises(ValueError):
        load_seed_file(bad)


def test_seed_database_upserts(engine):
    records = load_seed_file(FIXTURE)
    n = seed_database(engine, records)
    assert n == 2
    with session_scope(engine) as s:
        m = repo.get_mosque(s, "east-london-mosque")
        assert m.city == "London"
        assert m.website_url is None
