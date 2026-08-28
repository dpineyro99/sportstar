"""Regresión logística y corte temporal."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from sportstar.features.elo import GameResult
from sportstar.features.mlb import build_season_features
from sportstar.models import LogisticSportModel, temporal_split

T0 = datetime(2024, 4, 1, tzinfo=UTC)


def season(n: int = 400) -> list:
    """Temporada sintética donde el equipo 1 es claramente mejor."""
    games = []
    for i in range(n):
        home, away = (1, 2) if i % 2 == 0 else (2, 1)
        strong_wins = i % 5 != 0  # el equipo 1 gana el 80%
        strong_is_home = home == 1
        home_score, away_score = (5, 3) if (strong_wins == strong_is_home) else (3, 5)
        games.append(GameResult(2024, home, away, home_score, away_score, T0 + timedelta(days=i)))
    return build_season_features(games)


class TestTemporalSplit:
    def test_splits_by_time_never_at_random(self) -> None:
        """Barajar partidos mezcla futuro con pasado.

        El modelo aprendería de octubre para predecir junio y las métricas
        saldrían mejores de lo que serán en producción. Es la forma más común de
        engañarse en este dominio y la más difícil de detectar después.
        """
        train, test = temporal_split(season(), burn_in=0)
        assert train[-1].game.observed_at <= test[0].game.observed_at

    def test_respects_the_requested_fraction(self) -> None:
        train, test = temporal_split(season(400), train_fraction=0.75, burn_in=0)
        assert len(train) == pytest.approx(len(train) + len(test), rel=0.3)
        assert len(train) > len(test)

    def test_burn_in_drops_the_early_games(self) -> None:
        """En abril nadie tiene forma reciente ni récord: esas filas no son
        datos, son ruido con formato de dato."""
        without = temporal_split(season(), burn_in=0)
        with_burn = temporal_split(season(), burn_in=20)
        assert len(with_burn[0]) + len(with_burn[1]) < len(without[0]) + len(without[1])

    def test_every_surviving_row_meets_the_burn_in(self) -> None:
        train, test = temporal_split(season(), burn_in=20)
        assert all(r.min_games_played >= 20 for r in train + test)

    @pytest.mark.parametrize("fraction", [0.0, 1.0, -0.5, 1.5])
    def test_rejects_an_impossible_fraction(self, fraction: float) -> None:
        with pytest.raises(ValueError, match="train_fraction"):
            temporal_split(season(), train_fraction=fraction)


class TestLogisticModel:
    def test_learns_the_direction_of_the_signal(self) -> None:
        train, _ = temporal_split(season(), burn_in=20)
        model = LogisticSportModel().fit(train, ("elo_diff",))
        assert model.coefficients["elo_diff"] > 0

    def test_predictions_are_probabilities(self) -> None:
        train, test = temporal_split(season(), burn_in=20)
        model = LogisticSportModel().fit(train, ("elo_diff",))
        assert all(0.0 < p < 1.0 for p in model.predict_proba(test))

    def test_a_stronger_home_team_gets_a_higher_probability(self) -> None:
        train, test = temporal_split(season(), burn_in=20)
        model = LogisticSportModel().fit(train, ("elo_diff",))
        probabilities = model.predict_proba(test)
        by_edge = sorted(
            zip(test, probabilities, strict=True), key=lambda t: t[0].values["elo_diff"]
        )
        assert by_edge[0][1] < by_edge[-1][1]

    def test_coefficients_are_comparable_thanks_to_scaling(self) -> None:
        # Estandarizar no cambia las predicciones pero hace los coeficientes
        # comparables, y esos coeficientes son las razones que ve el usuario.
        train, _ = temporal_split(season(), burn_in=20)
        model = LogisticSportModel().fit(train, ("elo_diff", "rest_diff"))
        assert set(model.coefficients) == {"elo_diff", "rest_diff"}

    def test_records_its_training_provenance(self) -> None:
        train, _ = temporal_split(season(), burn_in=20)
        model = LogisticSportModel().fit(train, ("elo_diff",))
        assert model.n_train == len(train)
        assert model.trained_at is not None
        assert model.name == "mlb_logistic"

    def test_refuses_to_predict_before_training(self) -> None:
        with pytest.raises(RuntimeError, match="no está entrenado"):
            LogisticSportModel().predict_proba([])

    def test_refuses_to_train_on_nothing(self) -> None:
        with pytest.raises(ValueError, match="sin filas"):
            LogisticSportModel().fit([], ("elo_diff",))
