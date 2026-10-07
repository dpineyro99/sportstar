"""El motor: que los sanity checks bloqueen de verdad, y que el leakage se note.

El test central de este fichero construye una estrategia **tramposa a propósito**
—una que mira el resultado del partido que está prediciendo— y comprueba dos
cosas: que produce el backtest espectacular que uno esperaría, y que el sistema
lo señala en vez de celebrarlo. Un backtest que no puede distinguir entre un buen
modelo y un modelo con leakage no sirve para nada.
"""

from __future__ import annotations

import pytest

from sportstar.backtesting.dataset import HistoricalGame
from sportstar.backtesting.engine import BacktestResult, SanityBlocked, run_backtest
from sportstar.backtesting.strategies import Elo, EloBlend, MarketConsensus
from sportstar.validation.sanity import Severity

from .conftest import make_games


class Oracle:
    """Mira el resultado del partido que predice. Existe para ser detectada."""

    name = "oracle"
    version = "v-trampa"

    def predict_home(self, game: HistoricalGame) -> float:
        return 0.95 if game.home_won else 0.05

    def observe(self, game: HistoricalGame) -> None:
        pass


class TestElOracleSeDetecta:
    """Un modelo con leakage produce números imposibles, y el sistema lo dice."""

    @pytest.fixture(scope="class")
    @classmethod
    def result(cls) -> BacktestResult:
        return run_backtest(make_games(n_days=200), Oracle())

    def test_el_oracle_produce_un_backtest_espectacular(self, result: BacktestResult) -> None:
        # Se leen los candidates directamente porque las métricas están bloqueadas.
        bets = [c for c in result.candidates if c.is_recommended]
        assert bets
        assert sum(c.won for c in bets) / len(bets) > 0.90

    def test_y_por_eso_queda_bloqueado(self, result: BacktestResult) -> None:
        assert not result.passed_sanity
        assert "win_rate_vs_price" in {f.check for f in result.sanity.blocking}

    def test_el_edge_medio_no_lo_caza_y_conviene_saberlo(self, result: BacktestResult) -> None:
        """El check de edge mide la media **con signo**, y el oracle es simétrico.

        Apuesta a los dos lados con edge enorme y de signos opuestos, así que la
        media se cancela y ese check no salta. Lo caza `win_rate_vs_price`, que es
        suficiente — pero conviene tenerlo escrito: si algún día se cambia ese
        check, este backtest deja de estar protegido por donde uno creería.
        """
        assert "edge_distribution" not in {f.check for f in result.sanity.blocking}

    def test_las_metricas_no_se_pueden_leer(self, result: BacktestResult) -> None:
        with pytest.raises(SanityBlocked, match="no pasa los sanity checks"):
            _ = result.model
        with pytest.raises(SanityBlocked):
            _ = result.betting
        with pytest.raises(SanityBlocked):
            result.by_season()

    def test_el_informe_dice_que_esta_bloqueado_y_no_da_cifras(
        self, result: BacktestResult
    ) -> None:
        summary = result.summary()
        assert "BLOQUEADO" in summary
        assert "ROI" not in summary


def test_un_backtest_sano_si_muestra_metricas() -> None:
    result = run_backtest(make_games(n_days=150), MarketConsensus())

    assert result.passed_sanity
    assert result.model.n > 0
    assert "ROI" not in result.summary() or result.betting.n_bets == 0


def test_el_consenso_de_mercado_calibra_como_el_mercado() -> None:
    """Es la baseline: predice exactamente lo que dice el mercado."""
    result = run_backtest(make_games(n_days=150), MarketConsensus())

    assert result.model.brier_vs_market == pytest.approx(0.0, abs=1e-12)
    # No puede estar más cerca del cierre que ella misma.
    assert result.model.beat_market_rate == 0.0


def test_el_informe_avisa_de_los_gates_no_evaluables() -> None:
    result = run_backtest(make_games(n_days=100), MarketConsensus())

    summary = result.summary()
    assert "PARCIAL" in summary
    assert "line_freshness" in summary


def test_sin_partidos_no_hay_backtest() -> None:
    with pytest.raises(ValueError, match="no hay partidos"):
        run_backtest([], MarketConsensus())


def test_una_estrategia_que_nunca_opina_da_un_error_util() -> None:
    """El síntoma sería "0 apuestas", que se confunde con "no encontró nada"."""
    with pytest.raises(ValueError, match="min_games"):
        run_backtest(make_games(n_days=10), Elo(min_games=10_000))


def test_las_severidades_bloqueantes_son_fatales() -> None:
    result = run_backtest(make_games(n_days=200), Oracle())

    assert all(f.severity is Severity.FATAL for f in result.sanity.blocking)


def test_el_blend_produce_una_version_trazable() -> None:
    """Toda predicción tiene que saber qué modelo la produjo, con qué parámetros."""
    result = run_backtest(make_games(n_days=100), EloBlend(0.15))

    assert result.strategy_version == "v1-w0.15"
    assert all(c.strategy_version == "v1-w0.15" for c in result.candidates)


def test_un_peso_de_blend_invalido_falla_pronto() -> None:
    with pytest.raises(ValueError, match=r"\[0, 1\]"):
        EloBlend(1.5)
