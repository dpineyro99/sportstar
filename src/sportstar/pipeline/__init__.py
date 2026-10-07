"""Orquestación: de precios y predicciones a recomendaciones."""

from .candidates import CandidateEvaluation, evaluate_market, evaluate_selection
from .ingest import ingest_odds, ingest_schedule
from .persistence import PersistenceError, PersistResult, persist_evaluation, persist_evaluations
from .reasons import Reason, build_reasons

__all__ = [
    "CandidateEvaluation",
    "PersistResult",
    "PersistenceError",
    "Reason",
    "build_reasons",
    "evaluate_market",
    "evaluate_selection",
    "ingest_odds",
    "ingest_schedule",
    "persist_evaluation",
    "persist_evaluations",
]
