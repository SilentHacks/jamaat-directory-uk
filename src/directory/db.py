import threading
from collections.abc import Iterator
from contextlib import contextmanager
from importlib import resources
from pathlib import Path

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

# Serializes write transactions across threads. WAL lets readers run
# concurrently with the single writer; this lock removes "database is locked"
# races between competing writers in the thread-pooled ingest passes.
_write_lock = threading.Lock()


def make_engine(database_url: str) -> Engine:
    path = database_url.removeprefix("sqlite:///")
    if path and path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        database_url, future=True, connect_args={"check_same_thread": False}
    )

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_conn, _record):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()

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
def session_scope(engine: Engine, *, write: bool = False) -> Iterator[Session]:
    factory = sessionmaker(bind=engine, future=True)
    session = factory()
    if write:
        _write_lock.acquire()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        try:
            session.close()
        finally:
            if write:
                _write_lock.release()
