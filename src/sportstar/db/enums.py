"""Enumeraciones persistidas.

Se almacenan como VARCHAR + CHECK (`native_enum=False`) en vez de tipos ENUM
nativos: SQLite no los tiene y en Postgres alterarlos requiere migración. El
coste es un índice ligeramente mayor; la ventaja es que añadir un valor es una
migración trivial en ambos motores.
"""

from __future__ import annotations

from enum import StrEnum


class EventStatus(StrEnum):
    SCHEDULED = "scheduled"
    LIVE = "live"
    FINAL = "final"
    POSTPONED = "postponed"
    CANCELLED = "cancelled"


class MarketType(StrEnum):
    """Tipos de mercado. Moneyline es un caso particular, no el modelo base."""

    MONEYLINE = "moneyline"
    SPREAD = "spread"
    TOTAL = "total"
    TEAM_TOTAL = "team_total"
    PLAYER_PROP = "player_prop"


class Period(StrEnum):
    """Periodo del evento al que aplica el mercado."""

    GAME = "game"
    FIRST_HALF = "1H"
    SECOND_HALF = "2H"
    FIRST_QUARTER = "1Q"
    SECOND_QUARTER = "2Q"
    THIRD_QUARTER = "3Q"
    FOURTH_QUARTER = "4Q"
    INNINGS_1_5 = "innings_1_5"


class SubjectType(StrEnum):
    """A qué entidad se refiere una selección. Player props entran por aquí."""

    EVENT = "event"
    TEAM = "team"
    PLAYER = "player"


class Side(StrEnum):
    HOME = "home"
    AWAY = "away"
    OVER = "over"
    UNDER = "under"
    YES = "yes"
    NO = "no"
    DRAW = "draw"


class BookType(StrEnum):
    """Sharp define la probabilidad justa; recreational es donde se ejecuta."""

    SHARP = "sharp"
    RECREATIONAL = "recreational"
    EXCHANGE = "exchange"


class EntityType(StrEnum):
    TEAM = "team"
    PLAYER = "player"
    EVENT = "event"
    VENUE = "venue"


class ParticipantRole(StrEnum):
    STARTING_PITCHER = "starting_pitcher"
    RELIEF_PITCHER = "relief_pitcher"
    GOALIE = "goalie"
    QUARTERBACK = "quarterback"
    LINEUP = "lineup"
    STARTER = "starter"


class ParticipantStatus(StrEnum):
    PROJECTED = "projected"
    CONFIRMED = "confirmed"
    SCRATCHED = "scratched"


class BetOutcome(StrEnum):
    WIN = "win"
    LOSS = "loss"
    PUSH = "push"
    VOID = "void"


class RecommendationStatus(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    EXPIRED = "expired"


class JobStatus(StrEnum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class ReasonSource(StrEnum):
    """De dónde sale una contribución. Nunca hay una razón sin fuente."""

    MODEL_COEFFICIENT = "model_coefficient"
    SHAP = "shap"
    MARKET = "market"
