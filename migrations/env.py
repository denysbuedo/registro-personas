import os

from alembic import context

from registro_personas.db import make_engine
from registro_personas.models import Base

config = context.config
target_metadata = Base.metadata


def run(connection):
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    url = os.environ["REGISTRY_DATABASE_URL"]
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
elif config.attributes.get("connection") is not None:
    run(config.attributes["connection"])
else:
    engine = make_engine(os.environ["REGISTRY_DATABASE_URL"])
    with engine.connect() as connection:
        run(connection)
    engine.dispose()
