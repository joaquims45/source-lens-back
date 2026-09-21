from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session

from sourcelens.config import get_settings


@lru_cache
def engine() -> Engine:
    return create_engine(get_settings().database_url, pool_pre_ping=True)


def session() -> Session:
    return Session(engine(), expire_on_commit=False)
