"""Punto único de importación de todos los modelos.

Alembic descubre las tablas por `Base.metadata`, que solo se puebla cuando los
módulos se han importado. Este módulo garantiza ese import.
"""

from __future__ import annotations

from .base import Base
from .betting import Bet, BetResult, Candidate, Recommendation, RecommendationReason
from .catalog import (
    EntityAlias,
    ExternalId,
    League,
    Player,
    RawPayload,
    Sport,
    Sportsbook,
    Team,
    Venue,
)
from .events import Event, EventParticipant, Injury
from .markets import NO_LINE, AppendOnlyViolation, Market, OddsSnapshot, Selection
from .modeling import EventFeature, FeatureSet, ModelVersion, Prediction
from .ops import Backtest, BacktestBet, DataHealthCheck, JobRun, UnmatchedEntity

__all__ = [
    "NO_LINE",
    "AppendOnlyViolation",
    "Backtest",
    "BacktestBet",
    "Base",
    "Bet",
    "BetResult",
    "Candidate",
    "DataHealthCheck",
    "EntityAlias",
    "Event",
    "EventFeature",
    "EventParticipant",
    "ExternalId",
    "FeatureSet",
    "Injury",
    "JobRun",
    "League",
    "Market",
    "ModelVersion",
    "OddsSnapshot",
    "Player",
    "Prediction",
    "RawPayload",
    "Recommendation",
    "RecommendationReason",
    "Selection",
    "Sport",
    "Sportsbook",
    "Team",
    "UnmatchedEntity",
    "Venue",
]
