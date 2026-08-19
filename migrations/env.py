"""Entorno de Alembic.

La URL se toma de `SPORTSTAR_DATABASE_URL` en vez de `alembic.ini` para que
tests y producción no puedan divergir por un valor olvidado en el fichero.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context

from sportstar.db.models import Base
from sportstar.db.session import create_db_engine, database_url

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_db_engine()
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # SQLite no soporta ALTER completo: batch mode recrea la tabla. Sin
            # esto, cualquier downgrade que toque una columna falla.
            render_as_batch=True,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
