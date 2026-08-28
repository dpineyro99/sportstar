"""Selección: de candidate a recomendación."""

from .confidence import CONFIDENCE_VERSION, WEIGHTS, ConfidenceResult, compute_confidence
from .gates import FILTER_VERSION, FilterResult, GateInput, evaluate_gates

__all__ = [
    "CONFIDENCE_VERSION",
    "FILTER_VERSION",
    "WEIGHTS",
    "ConfidenceResult",
    "FilterResult",
    "GateInput",
    "compute_confidence",
    "evaluate_gates",
]
