"""Elo point-in-time."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from sportstar.features.elo import (
    DEFAULT_INITIAL_RATING,
    EloModel,
    GameResult,
    fit_through,
    win_probability,
)

T0 = datetime(2026, 4, 1, tzinfo=UTC)
HOME, AWAY = 1, 2


def game(day: int, home_score: int, away_score: int, season: int = 2026) -> GameResult:
    return GameResult(season, HOME, AWAY, home_score, away_score, T0 + timedelta(days=day))


class TestWinProbability:
    def test_equal_ratings_are_a_coin_flip(self) -> None:
        assert win_probability(0.0) == 0.5

    def test_four_hundred_points_is_ten_to_one(self) -> None:
        # La propiedad definitoria de la escala Elo.
        assert win_probability(400.0) == pytest.approx(10 / 11, abs=1e-9)

    def test_is_symmetric(self) -> None:
        assert win_probability(120.0) + win_probability(-120.0) == pytest.approx(1.0, abs=1e-12)


class TestEloUpdates:
    def test_starts_everyone_equal(self) -> None:
        model = EloModel()
        assert model.rating(HOME) == DEFAULT_INITIAL_RATING
        assert model.expected_home_win(HOME, AWAY) > 0.5  # ventaja local

    def test_winning_raises_and_losing_lowers(self) -> None:
        model = EloModel()
        model.update(game(1, 5, 3))
        assert model.rating(HOME) > DEFAULT_INITIAL_RATING
        assert model.rating(AWAY) < DEFAULT_INITIAL_RATING

    def test_is_zero_sum(self) -> None:
        # Lo que gana uno lo pierde el otro: el total del sistema no se mueve.
        model = EloModel()
        model.update(game(1, 5, 3))
        total = model.rating(HOME) + model.rating(AWAY)
        assert total == pytest.approx(2 * DEFAULT_INITIAL_RATING, abs=1e-9)

    def test_a_tie_moves_ratings_toward_each_other(self) -> None:
        model = EloModel()
        model.update(game(1, 3, 3))
        # El local era favorito por la ventaja de campo: empatar le resta.
        assert model.rating(HOME) < DEFAULT_INITIAL_RATING

    def test_beating_a_favourite_is_worth_more(self) -> None:
        """El tamaño del ajuste depende de la sorpresa, no del resultado."""
        expected = EloModel()
        expected.ratings = {HOME: 1700.0, AWAY: 1300.0}
        expected.update(game(1, 5, 3))
        gain_expected = expected.rating(HOME) - 1700.0

        upset = EloModel()
        upset.ratings = {HOME: 1300.0, AWAY: 1700.0}
        upset.update(game(1, 5, 3))
        gain_upset = upset.rating(HOME) - 1300.0

        assert gain_upset > gain_expected

    def test_margin_of_victory_is_ignored(self) -> None:
        # Deliberado en béisbol: una paliza dice poco más que una victoria justa,
        # y premiarla hace que el rating persiga el ruido.
        narrow, blowout = EloModel(), EloModel()
        narrow.update(game(1, 4, 3))
        blowout.update(game(1, 15, 0))
        assert narrow.rating(HOME) == blowout.rating(HOME)


class TestSeasonRegression:
    def test_ratings_regress_between_seasons(self) -> None:
        """Los equipos cambian de plantilla: arrastrar el rating entero
        sobrestima la continuidad."""
        model = EloModel()
        for day in range(30):
            model.update(game(day, 5, 3))
        peak = model.rating(HOME)

        model.update(GameResult(2027, HOME, AWAY, 5, 3, T0 + timedelta(days=200)))
        assert DEFAULT_INITIAL_RATING < model.rating(HOME) < peak

    def test_no_regression_within_a_season(self) -> None:
        model = EloModel()
        model.update(game(1, 5, 3))
        first = model.rating(HOME)
        model.update(game(2, 3, 5))
        # La segunda derrota baja el rating, pero no por regresión de temporada.
        assert model.rating(HOME) < first


class TestPointInTime:
    def test_only_uses_games_known_before_the_cutoff(self) -> None:
        games = [game(day, 5, 3) for day in range(20)]
        early = fit_through(games, T0 + timedelta(days=10))
        late = fit_through(games, T0 + timedelta(days=20))
        assert late.rating(HOME) > early.rating(HOME)
        assert early.sample_size(HOME) == 10
        assert late.sample_size(HOME) == 20

    def test_a_game_at_the_cutoff_is_excluded(self) -> None:
        # Estrictamente anterior: el resultado del propio partido que vamos a
        # predecir jamás puede entrar en sus features.
        games = [game(5, 5, 3)]
        assert fit_through(games, T0 + timedelta(days=5)).sample_size(HOME) == 0

    def test_future_games_never_leak_in(self) -> None:
        past = [game(day, 5, 3) for day in range(5)]
        future = [game(day, 3, 5) for day in range(10, 20)]
        only_past = fit_through(past, T0 + timedelta(days=6))
        with_future = fit_through(past + future, T0 + timedelta(days=6))
        assert only_past.rating(HOME) == with_future.rating(HOME)

    def test_result_does_not_depend_on_input_order(self) -> None:
        """Los partidos se ordenan por `observed_at`, no por el orden de la lista.

        Un backtest cuyo resultado dependa del orden en que salieron las filas de
        la base no es reproducible.
        """
        games = [game(day, 5, 3) for day in range(10)]
        forward = fit_through(games, T0 + timedelta(days=20))
        shuffled = fit_through(list(reversed(games)), T0 + timedelta(days=20))
        assert forward.rating(HOME) == pytest.approx(shuffled.rating(HOME), abs=1e-9)

    def test_orders_by_when_we_knew_not_when_it_happened(self) -> None:
        """Un resultado que llegó tarde no estaba disponible antes.

        El criterio es `observed_at`, y por eso un partido jugado antes pero
        conocido después queda fuera del corte.
        """
        late_arrival = GameResult(2026, HOME, AWAY, 5, 3, T0 + timedelta(days=30))
        model = fit_through([late_arrival], T0 + timedelta(days=10))
        assert model.sample_size(HOME) == 0

    def test_empty_history_yields_default_ratings(self) -> None:
        model = fit_through([], T0)
        assert model.rating(HOME) == DEFAULT_INITIAL_RATING
        assert model.sample_size(HOME) == 0
