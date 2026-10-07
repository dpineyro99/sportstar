"""Feature builder de MLB, contra la temporada 2024 real."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from sportstar.backfill import load_backfill
from sportstar.data.normalizers import normalize_schedule
from sportstar.features.elo import GameResult
from sportstar.features.mlb import (
    DEFAULT_MODEL_FEATURES,
    FEATURE_NAMES,
    build_season_features,
    to_game_results,
)
from sportstar.features.mlb.builder import MAX_REST_DAYS

T0 = datetime(2024, 4, 1, 18, 0, tzinfo=UTC)
HOME, AWAY = 1, 2


def game(day: int, home_score: int, away_score: int, home: int = HOME, away: int = AWAY):
    return GameResult(2024, home, away, home_score, away_score, T0 + timedelta(days=day))


class TestPointInTime:
    def test_the_first_game_has_no_history(self) -> None:
        row = build_season_features([game(0, 5, 3)])[0]
        assert row.values["elo_diff"] == 0.0
        assert row.values["form_diff"] == 0.0
        assert row.values["season_win_pct_diff"] == 0.0
        assert row.min_games_played == 0

    def test_features_never_see_their_own_result(self) -> None:
        """El orden dentro del bucle es lo único que separa un modelo honesto de
        uno que predice partidos que ya vio.

        Tras diez victorias locales, la fila del partido 11 refleja diez, no once.
        """
        rows = build_season_features([game(d, 5, 3) for d in range(11)])
        assert rows[-1].home_games_played == 10

    def test_history_accumulates_in_order(self) -> None:
        rows = build_season_features([game(d, 5, 3) for d in range(20)])
        diffs = [r.values["elo_diff"] for r in rows]
        assert diffs == sorted(diffs)  # el local gana siempre: sube monótono

    def test_input_order_does_not_matter(self) -> None:
        # Se ordena por `observed_at`, no por el orden en que salieron de la base.
        games = [game(d, 5, 3) for d in range(10)]
        forward = build_season_features(games)
        shuffled = build_season_features(list(reversed(games)))
        assert [r.values["elo_diff"] for r in forward] == [r.values["elo_diff"] for r in shuffled]


class TestFeatureSemantics:
    def test_a_winning_home_team_gets_positive_differences(self) -> None:
        rows = build_season_features([game(d, 5, 3) for d in range(15)])
        last = rows[-1]
        assert last.values["elo_diff"] > 0
        assert last.values["form_diff"] > 0
        assert last.values["season_win_pct_diff"] > 0

    def test_rest_is_capped(self) -> None:
        """El parón del All-Star mete huecos de una semana que no significan
        "muy descansado", solo "hubo parón". Sin tope dominarían el coeficiente.
        """
        rows = build_season_features([game(0, 5, 3), game(60, 5, 3)])
        assert abs(rows[1].values["rest_diff"]) <= MAX_REST_DAYS

    def test_a_debut_counts_as_fully_rested(self) -> None:
        row = build_season_features([game(0, 5, 3)])[0]
        assert row.values["rest_diff"] == 0.0  # ambos debutan

    def test_rest_difference_has_the_expected_sign(self) -> None:
        # El visitante jugó ayer; el local lleva días parado.
        games = [game(0, 3, 5, home=AWAY, away=3), game(4, 5, 3)]
        rows = build_season_features(games)
        assert rows[1].values["rest_diff"] > 0

    def test_label_is_the_home_win(self) -> None:
        rows = build_season_features([game(0, 5, 3), game(1, 3, 5)])
        assert [r.label for r in rows] == [1, 0]

    def test_vector_follows_the_requested_order(self) -> None:
        row = build_season_features([game(0, 5, 3)])[0]
        assert row.vector(("elo_diff", "rest_diff")) == [
            row.values["elo_diff"],
            row.values["rest_diff"],
        ]


class TestDefaultFeatureSet:
    def test_the_model_uses_a_subset_of_what_the_builder_computes(self) -> None:
        assert set(DEFAULT_MODEL_FEATURES) <= set(FEATURE_NAMES)

    def test_the_default_is_a_single_column(self) -> None:
        """Medido sobre 2024: las features de fuerza correlacionan entre 0.82 y
        0.93. No son varias señales, es una medida repetida — y con todas ellas
        cuatro de cinco coeficientes salían con el signo invertido.
        """
        assert DEFAULT_MODEL_FEATURES == ("elo_diff",)


class TestAgainstTheRealSeason:
    @pytest.fixture(scope="class")
    def rows(self) -> list:
        events = [e for p in load_backfill() for e in normalize_schedule(p).events]
        if not events:
            pytest.skip("sin histórico descargado")
        return build_season_features(to_game_results(events))

    def test_builds_a_row_per_competitive_game(self, rows: list) -> None:
        assert len(rows) == 2436

    def test_home_win_rate_matches_the_known_advantage(self, rows: list) -> None:
        # MLB 2024: ventaja local real, en el rango histórico.
        rate = sum(r.label for r in rows) / len(rows)
        assert 0.50 < rate < 0.56

    def test_every_feature_is_finite(self, rows: list) -> None:
        import math

        assert all(math.isfinite(v) for r in rows for v in r.values.values())

    def test_strength_features_are_collinear_in_what_the_model_sees(self, rows: list) -> None:
        """La razón por la que el modelo usa una sola columna, verificada.

        Se mide **sobre las filas post burn-in**, que son las que el modelo
        entrena. La distinción importa: sobre la temporada entera `elo_diff` y
        `season_win_pct_diff` correlacionan 0.66, por debajo del umbral; sobre lo
        que el modelo ve, 0.93.

        En abril el Elo apenas se ha movido de 1500 mientras el récord oscila con
        cinco partidos jugados; después ambos miden lo mismo. Diagnosticar sobre
        el conjunto equivocado habría dado el problema por inexistente.
        """
        from sportstar.models import temporal_split
        from sportstar.validation import correlation, find_collinear_pairs

        train, _ = temporal_split(rows, train_fraction=0.7)
        columns = {n: [r.values[n] for r in train] for n in FEATURE_NAMES}

        collinear = {frozenset((p.left, p.right)) for p in find_collinear_pairs(columns)}
        assert frozenset(("elo_diff", "season_win_pct_diff")) in collinear

        raw = {n: [r.values[n] for r in rows] for n in FEATURE_NAMES}
        assert abs(correlation(raw["elo_diff"], raw["season_win_pct_diff"])) < 0.80

    def test_rest_is_orthogonal_to_strength(self, rows: list) -> None:
        # Es la única feature que aporta una dimensión distinta, aunque su señal
        # sea débil.
        from sportstar.validation import correlation

        elo = [r.values["elo_diff"] for r in rows]
        rest = [r.values["rest_diff"] for r in rows]
        assert abs(correlation(elo, rest)) < 0.1
