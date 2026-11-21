import os
from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from .errors import DomainError


def make_engine(url: str) -> Engine:
    if not url.startswith("postgresql+psycopg://"):
        raise ValueError("PostgreSQL psycopg URL required")
    return create_engine(url, echo=False, hide_parameters=True, pool_pre_ping=True)


def database_is_ready() -> bool:
    """Return whether the configured PostgreSQL endpoint accepts a minimal query."""
    url = os.environ.get("REGISTRY_DATABASE_URL")
    if not url:
        return False
    try:
        with _engine_for(url).connect() as connection:
            return connection.execute(text("SELECT 1")).scalar_one() == 1
    except (SQLAlchemyError, OSError, ValueError):
        return False


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
