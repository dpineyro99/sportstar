"""Métricas: la separación modelo/filtro y el drawdown."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from sportstar.backtesting.metrics import (
    _drawdown,
    betting_performance,
    cut_by_edge_bucket,
    cut_by_season,
    model_performance,
    sharpe_like,
)
from sportstar.backtesting.replay import Candidate, replay
from sportstar.core.kelly import SizingMethod, Stake
from sportstar.filters.gates import FilterResult

from .conftest import make_games


def _candidate(
    *,
    side: str = "home",
    won: bool = True,
    units: float = 1.0,
    recommended: bool = True,
    total_edge: float = 0.03,
    season: int = 2011,
    model_prob: float = 0.6,
    taken: float = 2.0,
) -> Candidate:
    return Candidate(
        game_date=date(season, 4, 1),
        season=season,
        archive_sequence=1,
        side=side,
        home_team="A",
        away_team="B",
        strategy="s",
        strategy_version="v",
        model_prob=model_prob,
        market_fair_prob=0.5,
        taken_decimal=taken,
        closing_decimal=1.95,
        closing_fair_prob=0.52,
        edge=model_prob - 0.5,
        structural_edge=-0.01,
        total_edge=total_edge,
        expected_value=0.1,
        filter_result=FilterResult(passed=("min_edge",), failed=() if recommended else ("x",)),
        stake=Stake(units, SizingMethod.KELLY_FRACTIONAL, 0.2, units, was_capped=False),
        won=won,
        as_of=datetime(season, 4, 1, tzinfo=UTC),
        latest_input_observed_at=None,
    )


def test_el_drawdown_es_la_caida_desde_el_pico() -> None:
    # Sube a +3, cae a -1: la caída desde el pico es 4.
    assert _drawdown([2.0, 1.0, -2.0, -2.0]) == pytest.approx(4.0)


def test_sin_perdidas_no_hay_drawdown() -> None:
    assert _drawdown([1.0, 2.0, 3.0]) == 0.0


def test_el_drawdown_cuenta_desde_cero_si_se_empieza_perdiendo() -> None:
    assert _drawdown([-3.0, 1.0]) == pytest.approx(3.0)


def test_el_ratio_necesita_muestra_y_varianza() -> None:
    assert sharpe_like([1.0]) is None
    assert sharpe_like([]) is None
    # Varianza cero: el ratio sería infinito, y eso no es una medición.
    assert sharpe_like([1.0, 1.0, 1.0]) is None
    assert sharpe_like([1.0, -1.0, 1.0, -1.0]) is not None


def test_el_modelo_se_mide_solo_sobre_el_lado_local() -> None:
    """Los dos lados son la misma predicción del derecho y del revés."""
    candidates = [_candidate(side="home"), _candidate(side="away", won=False)]

    performance = model_performance(candidates)

    assert performance.n == 1


def test_el_filtro_se_mide_solo_sobre_lo_recomendado() -> None:
    candidates = [
        _candidate(won=True, units=1.0, recommended=True),
        _candidate(won=False, units=1.0, recommended=False),
    ]

    performance = betting_performance(candidates)

    assert performance.n_bets == 1
    assert performance.wins == 1


def test_un_stake_de_cero_no_es_una_apuesta() -> None:
    """Pasar los gates y que Kelly diga 0 units no es apostar."""
    performance = betting_performance([_candidate(units=0.0, recommended=True)])

    assert performance.n_bets == 0


def test_roi_y_win_rate_sobre_muestra_vacia_no_revientan() -> None:
    performance = betting_performance([_candidate(recommended=False)])

    assert performance.n_bets == 0
    assert performance.roi == 0.0
    assert performance.win_rate == 0.0


def test_el_roi_es_beneficio_sobre_lo_apostado() -> None:
    # Dos apuestas de 1 unit a cuota 2.0: una gana (+1), otra pierde (-1).
    performance = betting_performance(
        [_candidate(won=True, taken=2.0), _candidate(won=False, taken=2.0)]
    )

    assert performance.units_staked == pytest.approx(2.0)
    assert performance.units_profit == pytest.approx(0.0)
    assert performance.roi == pytest.approx(0.0)


def test_cortes_por_temporada() -> None:
    cuts = cut_by_season([_candidate(season=2011), _candidate(season=2012)])

    assert [c.label for c in cuts] == ["2011", "2012"]
    assert all(c.n_candidates == 1 for c in cuts)


def test_cortes_por_bucket_de_edge() -> None:
    """Responde a "¿qué edge mínimo funciona?", que es un criterio de salida."""
    cuts = cut_by_edge_bucket(
        [
            _candidate(total_edge=-0.01),
            _candidate(total_edge=0.005),
            _candidate(total_edge=0.025),
            _candidate(total_edge=0.10),
        ]
    )

    assert {c.label for c in cuts} == {"<0%", "0-1%", "2-3%", ">=5%"}


def test_los_buckets_de_edge_cubren_su_rango_real() -> None:
    """Una etiqueta que miente sobre su rango envenena la lectura del informe."""
    labels = {
        c.label: c
        for c in cut_by_edge_bucket(
            [_candidate(total_edge=e) for e in (0.0, 0.015, 0.035, 0.049, 0.05)]
        )
    }

    # 0,035 y 0,049 caen ambos en [3%, 5%), y la etiqueta tiene que decirlo.
    assert labels["3-5%"].n_candidates == 2
    assert labels[">=5%"].n_candidates == 1


def test_un_corte_sin_lado_local_no_intenta_medir_el_modelo() -> None:
    cuts = cut_by_edge_bucket([_candidate(side="away", total_edge=0.10)])

    assert cuts[0].model is None
    assert cuts[0].n_candidates == 1


def test_sin_candidates_el_modelo_no_se_puede_medir() -> None:
    with pytest.raises(ValueError, match="no hay candidates"):
        model_performance([])


def test_la_mejora_sobre_el_mercado_tiene_el_signo_correcto() -> None:
    """Positivo = el modelo aporta. Un signo invertido aquí lo cambiaría todo."""
    from sportstar.backtesting.strategies import MarketConsensus

    result = replay(make_games(n_days=60), MarketConsensus())
    performance = model_performance(result.candidates)

    # La baseline es el mercado: la mejora tiene que ser exactamente 0.
    assert performance.brier_vs_market == pytest.approx(0.0, abs=1e-12)
