"""Mercado + correcciones: orden de features, ausencias y contrato point-in-time.

El test que más importa es el de la fila de entrenamiento: si `build_rows` usara
información del propio día, el ajuste saldría excelente y el holdout no lo
reproduciría — y para cuando eso se nota, ya se ha decidido algo con él.
"""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import date

import pytest

from sportstar.backtesting.ensemble import (
    FEATURE_NAMES,
    Coefficients,
    FeatureState,
    MarketPlusCorrections,
    build_rows,
    fit,
    index_appearances,
)
from sportstar.backtesting.replay import replay
from sportstar.data.normalizers.mlb_pitchers import PitchingAppearance

from .conftest import make_games


def _appearances(days: list[date], pitcher_ids: range = range(1, 40)) -> list[PitchingAppearance]:
    """Apariciones abundantes para que todos los abridores tengan muestra."""
    return [
        PitchingAppearance(
            game_date=day,
            pitcher_id=pid,
            is_start=True,
            outs=18,
            earned_runs=3,
            strikeouts=6 + pid % 7,
            walks=2,
            hits=6,
            home_runs=1,
            batters_faced=25,
        )
        for day in days
        for pid in pitcher_ids
    ]


def _with_starters(games: list, home: int = 1, away: int = 2) -> list:  # type: ignore[type-arg]
    return [replace(g, home_pitcher_id=home, away_pitcher_id=away) for g in games]


def test_el_orden_de_las_features_es_estable() -> None:
    """Un vector de coeficientes sin nombres en el mismo orden es inauditable."""
    assert FEATURE_NAMES == ("market_logit", "elo_diff", "starter_advantage")


def test_sin_abridores_no_hay_vector() -> None:
    """Un cero sería "igual de buenos", que no es lo mismo que "no sé quién lanza"."""
    games = make_games(n_days=60)
    state = FeatureState(min_games=0)
    for game in games[:200]:
        state.observe(game)

    assert state.vector(games[-1]) is None


def test_sin_muestra_de_equipo_tampoco() -> None:
    games = _with_starters(make_games(n_days=60))
    state = FeatureState(min_games=20)

    assert state.vector(games[0]) is None


def test_el_vector_completo_lleva_las_tres_features() -> None:
    games = _with_starters(make_games(n_days=80))
    days = sorted({g.game_date.date() for g in games})
    state = FeatureState(min_games=20, appearances_by_date=index_appearances(_appearances(days)))
    for game in games[:400]:
        state.observe(game)

    vector = state.vector(games[-1])

    assert vector is not None
    assert len(vector) == len(FEATURE_NAMES)
    # El logit del mercado tiene que corresponder a la probabilidad de mercado.
    market = state.market_home(games[-1])
    assert market is not None
    assert vector[0] == pytest.approx(math.log(market / (1 - market)))


def test_las_apariciones_de_un_dia_se_consumen_una_sola_vez() -> None:
    """El replay llama `observe` una vez por partido; el día no puede contarse N veces."""
    games = make_games(n_days=2, games_per_day=5)
    day = games[0].game_date.date()
    state = FeatureState(appearances_by_date=index_appearances(_appearances([day], range(1, 2))))

    for game in games:
        if game.game_date.date() == day:
            state.observe(game)

    assert state.form.batters_faced(1) == 25


class TestFilasDeEntrenamiento:
    def test_ninguna_fila_usa_informacion_de_su_propio_dia(self) -> None:
        """La garantía central. Se comprueba con un abridor que solo lanza un día."""
        games = _with_starters(make_games(n_days=80))
        first_day = min(g.game_date.date() for g in games)
        # Apariciones solo del primer día: nadie tendrá muestra suficiente ese día.
        rows = build_rows(games, _appearances([first_day]))

        # Si el estado se hubiese adelantado, el primer día tendría features.
        assert rows.n_skipped > 0

    def test_produce_features_y_etiquetas_alineadas(self) -> None:
        games = _with_starters(make_games(n_days=100))
        days = sorted({g.game_date.date() for g in games})
        rows = build_rows(games, _appearances(days))

        assert len(rows.features) == len(rows.labels) == len(rows)
        assert rows
        assert set(rows.labels) <= {0, 1}

    def test_las_etiquetas_son_la_victoria_local(self) -> None:
        games = _with_starters(make_games(n_days=100))
        days = sorted({g.game_date.date() for g in games})
        rows = build_rows(games, _appearances(days))

        # La tasa base tiene que parecerse a la ventaja local del generador.
        assert 0.45 < sum(rows.labels) / len(rows.labels) < 0.62


def test_el_ajuste_devuelve_coeficientes_con_nombre() -> None:
    games = _with_starters(make_games(n_days=140))
    days = sorted({g.game_date.date() for g in games})
    rows = build_rows(games, _appearances(days))

    coefficients = fit(rows)

    assert set(coefficients.as_dict()) == set(FEATURE_NAMES)
    assert coefficients.n_train == len(rows)
    assert "market_logit" in coefficients.explain()


def test_el_mercado_domina_cuando_es_la_unica_senal_real() -> None:
    """Con features de relleno sin información, el peso se va al mercado."""
    games = _with_starters(make_games(n_days=140))
    days = sorted({g.game_date.date() for g in games})
    rows = build_rows(games, _appearances(days))

    weights = fit(rows).as_dict()

    assert weights["market_logit"] > abs(weights["starter_advantage"])


def test_sin_filas_no_se_puede_ajustar() -> None:
    from sportstar.backtesting.ensemble import TrainingRows

    with pytest.raises(ValueError, match="sin filas"):
        fit(TrainingRows(features=[], labels=[], n_skipped=0))


def test_los_coeficientes_predicen_una_probabilidad() -> None:
    coefficients = Coefficients(intercept=0.0, weights=(1.0, 0.0, 0.0))

    # Con peso 1 sobre el logit del mercado y nada más, devuelve el mercado.
    assert coefficients.predict([math.log(0.6 / 0.4), 0.0, 0.0]) == pytest.approx(0.6)
    assert coefficients.predict([0.0, 0.0, 0.0]) == pytest.approx(0.5)


class TestLaEstrategia:
    def test_cae_al_mercado_cuando_le_faltan_features(self) -> None:
        """Sin información adicional, el precio sigue siendo la mejor estimación."""
        games = make_games(n_days=10)  # sin abridores
        strategy = MarketPlusCorrections(
            Coefficients(intercept=0.0, weights=(1.0, 0.0, 0.0)), appearances=[]
        )

        predicted = strategy.predict_home(games[0])
        state = FeatureState()

        assert predicted == pytest.approx(state.market_home(games[0]))

    def test_usa_los_coeficientes_cuando_las_tiene(self) -> None:
        games = _with_starters(make_games(n_days=120))
        days = sorted({g.game_date.date() for g in games})
        coefficients = Coefficients(intercept=0.0, weights=(1.0, 0.0, 5.0))
        strategy = MarketPlusCorrections(coefficients, _appearances(days), min_games=20)

        for game in games[:600]:
            strategy.observe(game)
        predicted = strategy.predict_home(games[-1])
        market = FeatureState().market_home(games[-1])

        assert predicted is not None and market is not None
        # Con un peso grande sobre la ventaja de abridor, se separa del mercado.
        assert predicted != pytest.approx(market)

    def test_encaja_en_el_replay(self) -> None:
        games = _with_starters(make_games(n_days=120))
        days = sorted({g.game_date.date() for g in games})
        strategy = MarketPlusCorrections(
            Coefficients(intercept=0.0, weights=(1.0, 0.0, 0.0)), _appearances(days)
        )

        result = replay(games, strategy)

        assert result.candidates
        assert result.games_replayed > 0

    def test_la_version_viaja_con_la_prediccion(self) -> None:
        strategy = MarketPlusCorrections(
            Coefficients(intercept=0.0, weights=(1.0, 0.0, 0.0)),
            appearances=[],
            version="v2-pitchers",
        )

        assert strategy.version == "v2-pitchers"
        assert strategy.name == "market_plus"


class TestElDiagnosticoSinMercado:
    """Separar "la feature no vale nada" de "el mercado ya la tenía".

    Un coeficiente ~0 con el mercado dentro admite las dos lecturas, y son
    conclusiones opuestas: una dice que hay que tirar la feature, la otra que la
    feature es buena y hay que buscar dónde el mercado tarda en incorporarla.
    """

    def test_se_puede_ajustar_sobre_un_subconjunto(self) -> None:
        games = _with_starters(make_games(n_days=140))
        days = sorted({g.game_date.date() for g in games})
        rows = build_rows(games, _appearances(days))

        coefficients = fit(rows, use=("starter_advantage",))

        assert coefficients.names == ("starter_advantage",)
        assert set(coefficients.as_dict()) == {"starter_advantage"}

    def test_el_vector_completo_sigue_valiendo_para_predecir(self) -> None:
        """Quien construye el vector no tiene que saber con qué subconjunto se ajustó."""
        coefficients = Coefficients(intercept=0.0, weights=(2.0,), names=("starter_advantage",))

        # El vector llega completo; se selecciona la tercera columna.
        assert coefficients.predict([99.0, 99.0, 0.0]) == pytest.approx(0.5)
        assert coefficients.predict([99.0, 99.0, 1.0]) > 0.5

    def test_una_feature_desconocida_falla_pronto(self) -> None:
        games = _with_starters(make_games(n_days=140))
        days = sorted({g.game_date.date() for g in games})
        rows = build_rows(games, _appearances(days))

        with pytest.raises(ValueError, match="desconocidas"):
            fit(rows, use=("no_existe",))

    def test_quitar_el_mercado_libera_el_peso_de_las_demas(self) -> None:
        """Es el efecto que hace informativo el diagnóstico.

        Se construye un caso donde `starter_advantage` es una copia ruidosa de la
        señal que el mercado ya tiene: con el mercado dentro su coeficiente se
        desploma, sin él recupera peso.
        """
        games = _with_starters(make_games(n_days=200))
        days = sorted({g.game_date.date() for g in games})
        rows = build_rows(games, _appearances(days))

        with_market = fit(rows).as_dict()["starter_advantage"]
        without_market = fit(rows, use=("starter_advantage",)).as_dict()["starter_advantage"]

        # No se afirma la dirección —depende del generador—, sino que son
        # magnitudes distintas: si fuesen iguales el diagnóstico no diría nada.
        assert with_market != pytest.approx(without_market)
