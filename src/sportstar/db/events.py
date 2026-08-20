"""Eventos, participantes y lesiones."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, Enum, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from .base import Base, JsonDict, TimestampMixin, UtcDateTime
from .enums import EventStatus, ParticipantRole, ParticipantStatus


class Event(Base, TimestampMixin):
    __tablename__ = "events"
    # `game_number` forma parte de la clave porque un doubleheader es
    # literalmente "mismo día, mismos equipos, misma liga": sin él la constraint
    # prohíbe el segundo partido, y el sync fallaría en cada doblete de la
    # temporada. Es 1 para los partidos normales.
    __table_args__ = (
        UniqueConstraint("league_id", "event_date", "home_team_id", "away_team_id", "game_number"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    league_id: Mapped[int] = mapped_column(ForeignKey("leagues.id"), index=True)
    season: Mapped[int] = mapped_column(Integer, index=True)
    event_date: Mapped[date] = mapped_column(Date, index=True)
    game_number: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    start_time: Mapped[datetime] = mapped_column(UtcDateTime, index=True)
    actual_start_time: Mapped[datetime | None] = mapped_column(UtcDateTime)
    home_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True)
    away_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True)
    venue_id: Mapped[int | None] = mapped_column(ForeignKey("venues.id"))
    status: Mapped[EventStatus] = mapped_column(
        Enum(EventStatus, native_enum=False, length=16), default=EventStatus.SCHEDULED, index=True
    )
    home_score: Mapped[int | None] = mapped_column(Integer)
    away_score: Mapped[int | None] = mapped_column(Integer)
    weather: Mapped[JsonDict | None] = mapped_column(JSON)
    updated_at: Mapped[datetime | None] = mapped_column(UtcDateTime)


class EventParticipant(Base, TimestampMixin):
    """Starters, pitchers, goalies, QBs.

    `observed_at` es lo que permite responder honestamente "¿sabíamos el lineup
    confirmado cuando apostamos?". Sin ese campo, el backtest usa alineaciones
    que en su momento no existían — leakage clásico y difícil de detectar porque
    mejora los resultados de forma plausible.
    """

    __tablename__ = "event_participants"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), index=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True)
    role: Mapped[ParticipantRole] = mapped_column(
        Enum(ParticipantRole, native_enum=False, length=24)
    )
    batting_order: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[ParticipantStatus] = mapped_column(
        Enum(ParticipantStatus, native_enum=False, length=16)
    )
    observed_at: Mapped[datetime] = mapped_column(UtcDateTime, index=True)


class Injury(Base, TimestampMixin):
    __tablename__ = "injuries"

    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True)
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"), index=True)
    status: Mapped[str] = mapped_column(String(48))
    description: Mapped[str | None] = mapped_column(String(512))
    reported_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    observed_at: Mapped[datetime] = mapped_column(UtcDateTime, index=True)
