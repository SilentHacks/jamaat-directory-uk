from collections.abc import Iterator
from contextlib import contextmanager
from importlib import resources
from pathlib import Path

from sqlalchemy import Engine, create_engine, event, text
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
    # Strip line comments so a ';' inside a '-- ...' comment is invisible to the split.
    clean = "\n".join(line.split("--")[0] for line in sql.splitlines())
    with engine.begin() as conn:
        for statement in clean.split(";"):
            if statement.strip():
                conn.execute(text(statement))


@contextmanager
def session_scope(engine: Engine) -> Iterator[Session]:
    factory = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
