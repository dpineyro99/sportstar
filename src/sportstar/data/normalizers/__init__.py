"""Payload crudo -> estructuras canónicas. No emparejan con el catálogo."""

from .errors import ShapeError
from .mlb_schedule import normalize_schedule
from .models import NormalizationResult, NormalizedEvent, NormalizedPrice
from .odds_api import normalize_odds, parse_iso8601

__all__ = [
    "NormalizationResult",
    "NormalizedEvent",
    "NormalizedPrice",
    "ShapeError",
    "normalize_odds",
    "normalize_schedule",
    "parse_iso8601",
]
