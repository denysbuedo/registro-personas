from uuid import uuid4

from alembic import command
from sqlalchemy import create_engine, inspect, text

from .conftest import migration_config


def test_postgresql_upgrade_downgrade_and_metadata_match(test_database_url):
    schema = "per001_test_migration_" + uuid4().hex
    admin = create_engine(test_database_url, hide_parameters=True)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(
        test_database_url, hide_parameters=True, connect_args={"options": f"-csearch_path={schema}"}
    )
    try:
        with engine.begin() as connection:
            config = migration_config(connection)
            command.upgrade(config, "head")
            assert set(inspect(connection).get_table_names()) == {
                "persons",
                "source_references",
                "digital_accounts",
                "institutional_attributes",
                "idempotency_records",
                "review_cases",
                "audit_records",
                "alembic_version",
            }
            command.check(config)
            command.downgrade(config, "base")
            assert inspect(connection).get_table_names() == ["alembic_version"]
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname=:schema"
                    ),
                    {"schema": schema},
                )
                == 0
            )
            command.upgrade(config, "head")
            command.check(config)
            command.downgrade(config, "base")
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()
