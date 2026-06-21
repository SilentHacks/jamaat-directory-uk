import pytest
from sqlalchemy import text

from directory.db import init_db, make_engine, session_scope


def test_init_creates_all_tables(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path/'t.db'}")
    init_db(engine)
    with session_scope(engine) as s:
        rows = s.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        ).all()
    names = {r[0] for r in rows}
    assert {"mosque", "source", "occurrence", "extractor_run"} <= names


def test_init_is_idempotent(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path/'t.db'}")
    init_db(engine)
    init_db(engine)  # must not raise


def test_session_scope_commits(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path/'t.db'}")
    init_db(engine)
    with session_scope(engine) as s:
        s.execute(
            text(
                "INSERT INTO mosque (id, name, lat, lng) VALUES ('m1', 'A', 1.0, 2.0)"
            )
        )
    with session_scope(engine) as s:
        count = s.execute(text("SELECT count(*) FROM mosque")).scalar()
    assert count == 1


def test_foreign_keys_enforced(tmp_path):
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError
    engine = make_engine(f"sqlite:///{tmp_path/'t.db'}")
    init_db(engine)
    with pytest.raises(IntegrityError):
        with session_scope(engine) as s:
            s.execute(text(
                "INSERT INTO source (id, mosque_id) VALUES ('s1', 'missing')"
            ))
