"""Fábrica de histórico sintético para los tests del backtest.

Se genera con una probabilidad "verdadera" conocida y una semilla fija, de modo
que cada test pueda introducir **una** patología concreta y comprobar que salta
el check que le corresponde y solo ese.
"""

from __future__ import annotations

import random
from datetime import UTC, date, datetime, timedelta

import pytest

from sportstar.backtesting.dataset import HistoricalGame, MarketPrices
from sportstar.core.odds import implied_to_american

VIG_PER_SIDE = 0.013


def _two_way(fair: float, close_fair: float) -> tuple[MarketPrices, MarketPrices]:
    """Precios de local y visitante, con apertura y cierre y vig simétrico."""
    return (
        MarketPrices(
            implied_to_american(fair + VIG_PER_SIDE),
            implied_to_american(close_fair + VIG_PER_SIDE),
        ),
        MarketPrices(
            implied_to_american(1 - fair + VIG_PER_SIDE),
            implied_to_american(1 - close_fair + VIG_PER_SIDE),
        ),
    )


def make_games(
    n_days: int = 120,
    games_per_day: int = 8,
    *,
    seed: int = 5,
    n_teams: int = 30,
    start: date = date(2011, 4, 1),
    season: int = 2011,
    close_drift: float = 0.0,
) -> list[HistoricalGame]:
    """Histórico sintético con equipos de fuerza fija y mercado bien calibrado.

    `close_drift` desplaza el cierre respecto a la apertura, para simular un
    mercado que incorpora información entre una y otro.
    """
    rng = random.Random(seed)
    strength = {team: rng.gauss(0.0, 0.12) for team in range(n_teams)}
    games: list[HistoricalGame] = []

    for day_index in range(n_days):
        day = start + timedelta(days=day_index)
        teams = list(range(n_teams))
        rng.shuffle(teams)
        for slot in range(games_per_day):
            home_id, away_id = teams[slot * 2], teams[slot * 2 + 1]
            fair = min(0.80, max(0.20, 0.535 + strength[home_id] - strength[away_id]))
            close_fair = min(0.80, max(0.20, fair + close_drift))
            home_prices, away_prices = _two_way(fair, close_fair)
            home_won = rng.random() < fair

            games.append(
                HistoricalGame(
                    season=season,
                    game_date=datetime(day.year, day.month, day.day, tzinfo=UTC),
                    home_team_id=home_id,
                    away_team_id=away_id,
                    home_team=f"T{home_id}",
                    away_team=f"T{away_id}",
                    home_score=5 if home_won else 2,
                    away_score=2 if home_won else 5,
                    home=home_prices,
                    away=away_prices,
                )
            )
    return games


@pytest.fixture
def games() -> list[HistoricalGame]:
    return make_games()
