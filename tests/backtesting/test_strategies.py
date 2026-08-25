"""Las estrategias: qué predicen y, sobre todo, qué NO pueden ver."""

from __future__ import annotations

import pytest

from sportstar.backtesting.strategies import Elo, EloBlend, MarketConsensus

from .conftest import make_games


def test_el_consenso_devuelve_la_probabilidad_justa_de_apertura() -> None:
    game = make_games(n_days=1, games_per_day=1)[0]

    predicted = MarketConsensus().predict_home(game)

    assert predicted is not None
    fair = game.home.open_implied / (game.home.open_implied + game.away.open_implied)
    assert predicted == pytest.approx(fair)


def test_el_consenso_usa_la_apertura_y_no_el_cierre() -> None:
    """Usar el cierre lo convertiría en un oráculo: es información futura."""
    games = make_games(n_days=1, games_per_day=1, close_drift=0.15)
    game = games[0]

    predicted = MarketConsensus().predict_home(game)

    assert predicted is not None
    closing_fair = game.home.close_implied / (game.home.close_implied + game.away.close_implied)
    assert abs(predicted - closing_fair) > 0.05


def test_el_consenso_no_aprende_de_resultados() -> None:
    strategy = MarketConsensus()
    game = make_games(n_days=1, games_per_day=1)[0]
    before = strategy.predict_home(game)

    strategy.observe(game)

    assert strategy.predict_home(game) == before


def test_elo_calla_hasta_tener_muestra() -> None:
    strategy = Elo(min_games=20)
    # 50 jornadas x 8 partidos entre 30 equipos son ~27 partidos por equipo:
    # suficiente para superar el umbral, y no mucho más.
    games = make_games(n_days=50, games_per_day=8)

    assert strategy.predict_home(games[0]) is None

    for game in games:
        strategy.observe(game)

    assert strategy.predict_home(games[0]) is not None


def test_elo_da_mas_probabilidad_al_equipo_mejor() -> None:
    strategy = Elo(min_games=0)
    games = make_games(n_days=60, games_per_day=8)
    for game in games:
        strategy.observe(game)

    ratings = strategy._model.ratings
    best = max(ratings, key=lambda t: ratings[t])
    worst = min(ratings, key=lambda t: ratings[t])
    strong_at_home = (
        next(g for g in games if g.home_team_id == best and g.away_team_id == worst)
        if any(g.home_team_id == best and g.away_team_id == worst for g in games)
        else None
    )

    if strong_at_home is not None:
        predicted = strategy.predict_home(strong_at_home)
        assert predicted is not None and predicted > 0.5
    assert ratings[best] > ratings[worst]


def test_el_blend_con_peso_cero_es_el_mercado() -> None:
    game = make_games(n_days=1, games_per_day=1)[0]

    assert EloBlend(0.0).predict_home(game) == MarketConsensus().predict_home(game)


def test_el_blend_cae_al_mercado_mientras_elo_calla() -> None:
    """Al principio de temporada Elo es ruido; el blend no debe propagarlo."""
    game = make_games(n_days=1, games_per_day=1)[0]

    assert EloBlend(0.50, min_games=20).predict_home(game) == MarketConsensus().predict_home(game)


def test_el_blend_se_mueve_hacia_elo_cuando_elo_opina() -> None:
    blend = EloBlend(0.50, min_games=0)
    games = make_games(n_days=60, games_per_day=8)
    for game in games:
        blend.observe(game)

    market = MarketConsensus().predict_home(games[0])
    blended = blend.predict_home(games[0])
    elo = blend._elo.predict_home(games[0])

    assert market is not None and blended is not None and elo is not None
    assert blended == pytest.approx(0.5 * market + 0.5 * elo)


def test_la_version_del_blend_incluye_el_peso() -> None:
    """Cada predicción tiene que saber con qué parámetros se produjo."""
    assert EloBlend(0.25).version == "v1-w0.25"
    assert EloBlend(0.05).version != EloBlend(0.10).version


def test_un_peso_fuera_de_rango_falla_al_construir() -> None:
    with pytest.raises(ValueError, match=r"\[0, 1\]"):
        EloBlend(-0.1)
