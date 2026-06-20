from sqlalchemy import Engine

from directory.config import get_settings
from directory.db import make_engine

_engine: Engine | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = make_engine(get_settings().database_url)
    return _engine
