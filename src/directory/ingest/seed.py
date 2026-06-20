import json
from pathlib import Path

from sqlalchemy import Engine

from directory import repository as repo
from directory.db import session_scope

_REQUIRED = ("id", "name", "lat", "lng")


def load_seed_file(path: Path) -> list[dict]:
    data = json.loads(Path(path).read_text())
    if not isinstance(data, list):
        raise ValueError("seed file must be a JSON array")
    for i, record in enumerate(data):
        missing = [k for k in _REQUIRED if record.get(k) is None]
        if missing:
            raise ValueError(f"record {i} missing required field(s): {missing}")
    return data


def seed_database(engine: Engine, mosques: list[dict]) -> int:
    with session_scope(engine) as s:
        return repo.upsert_mosques(s, mosques)
