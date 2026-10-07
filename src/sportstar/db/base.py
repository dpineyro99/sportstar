"""Base declarativa y convenciones comunes.

Todos los timestamps son **UTC y con zona horaria**, garantizado por el tipo
`UtcDateTime` en vez de por disciplina.

Por qué hace falta un tipo propio: SQLite no almacena la zona horaria, así que
`DateTime(timezone=True)` devuelve datetimes *naive* al leer. Compararlos con un
`as_of` con zona lanza `TypeError`, y todo el pipeline point-in-time vive de esa
comparación. Peor: en Postgres sí funcionaría, de modo que el comportamiento
dependería del motor y el fallo aparecería solo al migrar.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, TypeAlias

from sqlalchemy import DateTime, Dialect, MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

# Columnas JSON. Los alias existen para que `mypy --strict` tenga genéricos
# parametrizados sin sembrar `Any` suelto por todos los modelos.
JsonDict: TypeAlias = dict[str, Any]
JsonList: TypeAlias = list[Any]
JsonValue: TypeAlias = "JsonDict | JsonList"

# Nombres deterministas para índices y constraints: sin esto, Alembic genera
# nombres autogenerados que difieren entre motores y hacen los downgrades frágiles.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_N_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s",
    "pk": "pk_%(table_name)s",
}


class UtcDateTime(TypeDecorator[datetime]):
    """`DateTime` que garantiza UTC con zona horaria en ambos sentidos.

    Al escribir **rechaza** los datetimes naive en vez de asumir que ya son UTC.
    Asumirlo es lo que produce desfases de horas que nadie detecta hasta que un
    evento aparece capturado después de su propio inicio.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError(
                f"timestamp sin zona horaria: {value!r}. Todo el sistema trabaja en UTC "
                "con zona explícita; un naive aquí produce desfases silenciosos."
            )
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        # SQLite devuelve naive: el valor almacenado es UTC, así que se reetiqueta.
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TimestampMixin:
    """`created_at` gestionado por la base de datos."""

    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime, server_default=func.now(), nullable=False
    )
