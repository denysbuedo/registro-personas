import os
from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from .errors import DomainError


def make_engine(url: str) -> Engine:
    if not url.startswith("postgresql+psycopg://"):
        raise ValueError("PostgreSQL psycopg URL required")
    return create_engine(url, echo=False, hide_parameters=True, pool_pre_ping=True)


def get_session() -> Iterator[Session]:
    url = os.environ.get("REGISTRY_DATABASE_URL")
    if not url:
        raise DomainError(503, "database-unavailable", "Persistencia no disponible")
    # Lazily initialize: denied requests need no database connectivity.
    engine = _engine_for(url)
    with Session(engine, expire_on_commit=False) as session:
        yield session


@lru_cache(maxsize=1)
def _engine_for(url: str) -> Engine:
    return make_engine(url)
