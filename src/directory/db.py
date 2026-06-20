from collections.abc import Iterator
from contextlib import contextmanager
from importlib import resources
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker


def make_engine(database_url: str) -> Engine:
    path = database_url.removeprefix("sqlite:///")
    if path and path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(database_url, future=True)

    @event.listens_for(engine, "connect")
    def _enable_fk(dbapi_conn, _record):  # noqa: ANN001
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    return engine


def init_db(engine: Engine) -> None:
    sql = resources.files("directory").joinpath("schema.sql").read_text()
    with engine.connect() as conn:
        conn.connection.executescript(sql)  # type: ignore[attr-defined]


@contextmanager
def session_scope(engine: Engine) -> Iterator[Session]:
    factory = sessionmaker(bind=engine, future=True)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
