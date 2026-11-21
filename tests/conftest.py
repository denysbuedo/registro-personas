import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from registro_personas.models import Base
from registro_personas.schemas import PersonCreate
from registro_personas.security import Principal
from registro_personas.service import Registry

from .database_guard import validate_test_url

ROOT = Path(__file__).resolve().parents[1]


def migration_config(connection):
    config = Config(str(ROOT / "alembic.ini"))
    config.attributes["connection"] = connection
    return config


@pytest.fixture(scope="session")
def test_database_url():
    value = os.environ.get("REGISTRY_TEST_DATABASE_URL", "")
    try:
        return validate_test_url(value)
    except ValueError as error:
        pytest.fail(str(error))


@pytest.fixture(scope="session")
def migrated_engine(test_database_url):
    schema = "per001_test_" + uuid4().hex
    admin = create_engine(test_database_url, hide_parameters=True)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(
        test_database_url, hide_parameters=True, connect_args={"options": f"-csearch_path={schema}"}
    )
    with engine.begin() as connection:
        command.upgrade(migration_config(connection), "head")
    yield engine
    with engine.begin() as connection:
        command.downgrade(migration_config(connection), "base")
    engine.dispose()
    with admin.begin() as connection:
        # Generated schema within dedicated, validated local test database only.
        connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    admin.dispose()


@pytest.fixture
def db_engine(migrated_engine):
    tables = ", ".join(f'"{table.name}"' for table in Base.metadata.sorted_tables)
    with migrated_engine.begin() as connection:
        connection.execute(text(f"TRUNCATE {tables} CASCADE"))
    return migrated_engine


@pytest.fixture
def principal():
    return Principal(
        actor_id=uuid4(),
        permissions=frozenset({"persons.create", "persons.read", "persons.list", "accounts.link"}),
        source_systems=frozenset({"demo"}),
    )


@pytest.fixture
def person_data():
    return PersonCreate.model_validate(
        {
            "display_name": "Persona Ficticia",
            "source": {"system": "demo", "entity": "fixture", "external_id": "SYNTHETIC-001"},
            "provenance": {
                "system": "demo",
                "evidence_ref": str(uuid4()),
                "observed_at": "2026-11-19T12:00:00Z",
            },
        }
    )


def invoke(engine, principal, method, *args):
    with Session(engine, expire_on_commit=False) as session:
        service = Registry(session, principal, uuid4())
        return getattr(service, method)(*args)
