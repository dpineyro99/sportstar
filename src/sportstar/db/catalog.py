"""Catálogo y resolución de entidades.

`external_ids` y `entity_aliases` son el corazón del entity matching. Emparejar
el evento del proveedor de odds con el del proveedor de stats es donde se va la
mitad del mantenimiento real de un sistema como este: "NY Yankees" vs
"New York Yankees" vs "NYY", partidos dobles, cambios de horario, suspensiones.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Enum, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from .base import Base, JsonDict, JsonValue, TimestampMixin, UtcDateTime
from .enums import BookType, EntityType


class Sport(Base, TimestampMixin):
    __tablename__ = "sports"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(32), unique=True)  # mlb, nba, nfl...
    name: Mapped[str] = mapped_column(String(64))


class League(Base, TimestampMixin):
    __tablename__ = "leagues"
    __table_args__ = (UniqueConstraint("sport_id", "key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    sport_id: Mapped[int] = mapped_column(ForeignKey("sports.id"), index=True)
    key: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(96))
    season_type: Mapped[str | None] = mapped_column(String(32))


class Venue(Base, TimestampMixin):
    __tablename__ = "venues"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    city: Mapped[str | None] = mapped_column(String(96))
    tz: Mapped[str | None] = mapped_column(String(64))
    altitude_m: Mapped[float | None] = mapped_column(Float)
    roof: Mapped[str | None] = mapped_column(String(32))
    park_factors: Mapped[JsonDict | None] = mapped_column(JSON)


class Team(Base, TimestampMixin):
    __tablename__ = "teams"
    __table_args__ = (UniqueConstraint("league_id", "key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    league_id: Mapped[int] = mapped_column(ForeignKey("leagues.id"), index=True)
    key: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(96))
    abbreviation: Mapped[str | None] = mapped_column(String(8))
    conference: Mapped[str | None] = mapped_column(String(32))
    division: Mapped[str | None] = mapped_column(String(32))
    venue_id: Mapped[int | None] = mapped_column(ForeignKey("venues.id"))


class Player(Base, TimestampMixin):
    __tablename__ = "players"

    id: Mapped[int] = mapped_column(primary_key=True)
    league_id: Mapped[int] = mapped_column(ForeignKey("leagues.id"), index=True)
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"), index=True)
    full_name: Mapped[str] = mapped_column(String(128), index=True)
    position: Mapped[str | None] = mapped_column(String(16))
    bats: Mapped[str | None] = mapped_column(String(4))
    throws: Mapped[str | None] = mapped_column(String(4))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class ExternalId(Base, TimestampMixin):
    """ID de una entidad en un proveedor externo.

    Sin esta tabla, cada proveedor nuevo obliga a tocar el esquema.
    """

    __tablename__ = "external_ids"
    __table_args__ = (UniqueConstraint("entity_type", "provider", "provider_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_type: Mapped[EntityType] = mapped_column(Enum(EntityType, native_enum=False, length=16))
    entity_id: Mapped[int] = mapped_column(Integer, index=True)
    provider: Mapped[str] = mapped_column(String(64))
    provider_id: Mapped[str] = mapped_column(String(128))


class EntityAlias(Base, TimestampMixin):
    """Nombre alternativo con el que un proveedor se refiere a una entidad."""

    __tablename__ = "entity_aliases"
    __table_args__ = (UniqueConstraint("entity_type", "alias", "source"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_type: Mapped[EntityType] = mapped_column(Enum(EntityType, native_enum=False, length=16))
    entity_id: Mapped[int] = mapped_column(Integer, index=True)
    alias: Mapped[str] = mapped_column(String(160), index=True)
    source: Mapped[str] = mapped_column(String(64))
    confidence: Mapped[float] = mapped_column(Float, default=1.0)


class Sportsbook(Base, TimestampMixin):
    """Casa de apuestas.

    Los dos flags codifican la separación de ARCHITECTURE.md §4.2:
    `is_reference` decide quién define la probabilidad justa, `is_executable`
    decide dónde se puede apostar de verdad. Un book puede ser ninguno de los
    dos y seguir siendo útil para medir dispersión.
    """

    __tablename__ = "sportsbooks"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(48), unique=True)
    name: Mapped[str] = mapped_column(String(96))
    book_type: Mapped[BookType] = mapped_column(Enum(BookType, native_enum=False, length=16))
    is_reference: Mapped[bool] = mapped_column(Boolean, default=False)
    is_executable: Mapped[bool] = mapped_column(Boolean, default=False)
    region: Mapped[str | None] = mapped_column(String(32))
    # Operador detrás de la marca. Dos marcas del mismo operador publican el
    # mismo precio, así que promediarlas no es un consenso de dos: es una
    # opinión contada dos veces, con dispersión cero y confianza inflada.
    #
    # Medido sobre datos reales: LowVig.ag y BetOnline.ag coincidieron en 26 de
    # 28 precios (93%). Son la misma casa.
    operator_group: Mapped[str] = mapped_column(String(48), index=True)


class RawPayload(Base):
    """Respuesta íntegra de un proveedor. Inmutable, nunca se parsea aquí.

    Permite reprocesar todo el histórico cuando un normalizador tenga un bug,
    sin volver a pagar la API. `observed_at` es la base del contrato
    point-in-time: fija cuándo el hecho estuvo disponible **para nosotros**, que
    es lo que importa para el backtest — no la fecha nominal del hecho.
    """

    __tablename__ = "raw_payloads"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(64), index=True)
    endpoint: Mapped[str] = mapped_column(String(160))
    sport_key: Mapped[str | None] = mapped_column(String(32), index=True)
    payload: Mapped[JsonValue] = mapped_column(JSON)
    requested_at: Mapped[datetime] = mapped_column(UtcDateTime)
    observed_at: Mapped[datetime] = mapped_column(UtcDateTime, index=True)
    http_status: Mapped[int | None] = mapped_column(Integer)
    run_id: Mapped[str | None] = mapped_column(String(64), index=True)
