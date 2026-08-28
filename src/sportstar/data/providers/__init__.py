"""Adaptadores de APIs externas. Solo traen bytes."""

from .base import DataProvider, RawFetch
from .mlb_stats_api import MlbStatsApiProvider
from .the_odds_api import MARKET_KEYS, SPORT_KEYS, TheOddsApiProvider

__all__ = [
    "MARKET_KEYS",
    "SPORT_KEYS",
    "DataProvider",
    "MlbStatsApiProvider",
    "RawFetch",
    "TheOddsApiProvider",
]
