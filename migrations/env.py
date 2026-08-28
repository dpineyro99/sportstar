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


def render_item(type_: str, obj: object, autogen_context: object) -> str | bool:
    """Renderiza `UtcDateTime` como el DDL que realmente es.

    `UtcDateTime` es un `TypeDecorator`: solo afecta a la conversión del lado de
    Python, no a la columna. Sin esto, autogenerate escribiría
    `sportstar.db.base.UtcDateTime()` en la migración sin importarlo, y además
    acoplaría el histórico de migraciones a una clase de la aplicación —que puede
    moverse o cambiar de nombre, rompiendo migraciones ya aplicadas.
    """
    if type_ == "type" and type(obj).__name__ == "UtcDateTime":
        return "sa.DateTime(timezone=True)"
    return False


def run_migrations_offline() -> None:
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
        render_item=render_item,
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
            render_item=render_item,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
