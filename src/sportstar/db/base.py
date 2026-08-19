"""Base declarativa y convenciones comunes.

Todos los timestamps son **UTC**. SQLite no almacena zona horaria, así que la
convención se sostiene por disciplina en la capa de aplicación: nunca se escribe
un `datetime` naive que no sea UTC.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, TypeAlias

from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

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


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def utc_timestamp() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False)


class TimestampMixin:
    """`created_at` gestionado por la base de datos."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
