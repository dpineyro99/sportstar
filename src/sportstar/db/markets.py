"""Mercados, selecciones y snapshots de odds.

`odds_snapshots` es append-only y está protegida por un guard a nivel de ORM
(`_block_mutation`). No es un adorno: es el activo más valioso del sistema y el
único que no se puede reconstruir a posteriori. El precio de ayer se perdió ayer.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import Mapped, Mapper, mapped_column

from .base import Base, TimestampMixin, UtcDateTime
from .enums import MarketType, Period, Side, SubjectType


class Market(Base, TimestampMixin):
    """Tipo de mercado por deporte y periodo. No incluye la línea concreta."""

    __tablename__ = "markets"
    __table_args__ = (UniqueConstraint("sport_id", "market_type", "period"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    sport_id: Mapped[int] = mapped_column(ForeignKey("sports.id"), index=True)
    market_type: Mapped[MarketType] = mapped_column(Enum(MarketType, native_enum=False, length=16))
    period: Mapped[Period] = mapped_column(Enum(Period, native_enum=False, length=16))
    description: Mapped[str | None] = mapped_column(String(160))


# En SQL `NULL != NULL`, así que una UNIQUE que incluya columnas nulables no
# restringe las filas donde son NULL. Con `line` y `subject_id` nulables, dos
# selecciones de moneyline idénticas entrarían sin error — y de ahí salen
# apuestas duplicadas y un backtest que cuenta el mismo partido dos veces. Por
# eso ambas columnas son NOT NULL con centinela explícito.
NO_LINE = 0.0
"""Centinela de `Selection.line` en mercados sin línea (moneyline).

No significa "línea de 0": significa "este mercado no tiene línea". Un spread de
0.0 (pick'em) es legítimo y no colisiona, porque `market_id` ya distingue el tipo
de mercado dentro de la constraint.
"""


class Selection(Base, TimestampMixin):
    """Un lado apostable concreto de un mercado de un evento.

    La clave canónica de ARCHITECTURE.md §2.3. Añadir player props en Phase 9 es
    rellenar `subject_type='player'`; sin esta forma, sería una migración con
    reescritura de todo el pipeline.

    `subject_id` apunta a la entidad del `subject_type`: un `team_id`, un
    `player_id`, o el propio `event_id` cuando el sujeto es el evento.
    """

    __tablename__ = "selections"
    __table_args__ = (
        UniqueConstraint("event_id", "market_id", "subject_type", "subject_id", "side", "line"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), index=True)
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id"), index=True)
    subject_type: Mapped[SubjectType] = mapped_column(
        Enum(SubjectType, native_enum=False, length=12)
    )
    subject_id: Mapped[int] = mapped_column(Integer, nullable=False)
    side: Mapped[Side] = mapped_column(Enum(Side, native_enum=False, length=8))
    line: Mapped[float] = mapped_column(Float, nullable=False, default=NO_LINE)


class OddsSnapshot(Base):
    """Observación de un precio en un instante. APPEND ONLY.

    Sin `UPDATE`, sin `DELETE`, sin excepciones. Si un precio cambia, es una fila
    nueva. Todo lo demás (opening, current, closing, best available, consensus,
    line movement) son vistas derivadas de esta tabla.

    El snapshot tomado al `start_time` se captura para **todas** las selecciones
    observadas, no solo las apostadas: es lo que convierte esta tabla en el
    dataset de validación del sistema (ARCHITECTURE.md §4.6) y no solo en el
    registro de precios.
    """

    __tablename__ = "odds_snapshots"
    __table_args__ = (
        Index("ix_odds_snapshots_sel_book_time", "selection_id", "sportsbook_id", "captured_at"),
        Index("ix_odds_snapshots_captured_at", "captured_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    selection_id: Mapped[int] = mapped_column(ForeignKey("selections.id"))
    sportsbook_id: Mapped[int] = mapped_column(ForeignKey("sportsbooks.id"))
    price_american: Mapped[float] = mapped_column(Float)
    price_decimal: Mapped[float] = mapped_column(Float)
    # La línea se congela en el snapshot aunque también viva en `selections`:
    # los books mueven la línea, y el precio solo tiene sentido junto a la suya.
    # `NO_LINE` en mercados sin línea, por coherencia con `Selection.line`.
    line: Mapped[float] = mapped_column(Float, nullable=False, default=NO_LINE)
    implied_prob: Mapped[float] = mapped_column(Float)  # CON vig, tal cual viene
    is_available: Mapped[bool] = mapped_column(Boolean, default=True)
    captured_at: Mapped[datetime] = mapped_column(UtcDateTime)
    run_id: Mapped[str | None] = mapped_column(String(64), index=True)


class AppendOnlyViolation(RuntimeError):
    """Se intentó mutar una tabla append-only."""


def _block_mutation(mapper: Mapper[Any], connection: Any, target: Any) -> None:
    raise AppendOnlyViolation(
        f"{type(target).__name__} es append-only: un precio histórico nunca se "
        "sobrescribe ni se borra. Si el precio cambió, inserta un snapshot nuevo."
    )


# El guard vive en el ORM, no solo en la documentación. Un trigger en la base de
# datos sería más fuerte, pero rompería las migraciones de Alembic y el borrado
# legítimo de datos de prueba; a este nivel el error salta en desarrollo, que es
# donde se comete el fallo.
event.listen(OddsSnapshot, "before_update", _block_mutation)
event.listen(OddsSnapshot, "before_delete", _block_mutation)
