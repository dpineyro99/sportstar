"""El comando: que la comparación se ordene bien y que el test set no se toque solo."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from sportstar.backtesting import run as run_module
from sportstar.backtesting.engine import run_backtest
from sportstar.backtesting.run import Comparison, compare, default_strategies
from sportstar.backtesting.splits import HoldoutLedger
from sportstar.backtesting.strategies import MarketConsensus

from .conftest import make_games


def _comparison(*, brier: float, market_brier: float, beat: float) -> Comparison:
    return Comparison(
        strategy="s",
        version="v",
        n=1000,
        brier=brier,
        market_brier=market_brier,
        beat_market_rate=beat,
        z_score=0.0,
        n_bets=0,
        roi=0.0,
    )


def test_promocionar_exige_las_dos_condiciones() -> None:
    """Mejor Brier O más cerca del cierre no basta: hacen falta las dos."""
    assert _comparison(brier=0.240, market_brier=0.242, beat=0.52).beats_market
    # Mejor Brier pero no más cerca del cierre: puede ser solo mejor calibrado.
    assert not _comparison(brier=0.240, market_brier=0.242, beat=0.49).beats_market
    # Más cerca del cierre pero peor Brier: puede ser ruido.
    assert not _comparison(brier=0.244, market_brier=0.242, beat=0.52).beats_market


def test_la_baseline_de_mercado_no_se_promociona_a_si_misma() -> None:
    result = run_backtest(make_games(n_days=120), MarketConsensus())

    row = compare(result)

    assert row.brier_vs_market == pytest.approx(0.0, abs=1e-12)
    assert not row.beats_market


def test_el_z_mide_contra_la_moneda() -> None:
    """Bajo H0 la tasa es 0,5; el z dice cuántos errores típicos se aleja."""
    result = run_backtest(make_games(n_days=120), MarketConsensus())

    row = compare(result)

    # La baseline da exactamente 0, que a n grande está muchísimos sigmas por
    # debajo de 0,5. Un z cercano a 0 aquí significaría que el cálculo está mal.
    assert row.z_score < -30


def test_la_lista_por_defecto_incluye_la_baseline() -> None:
    """Sin el listón, cualquier modelo "funciona"."""
    names = [s.name for s in default_strategies()]

    assert "market_consensus" in names
    assert len(names) > 1


class FakeHistory:
    def __init__(self, games: list) -> None:  # type: ignore[type-arg]
        self.games = games


def _patch_history(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sustituye la descarga real por histórico sintético de dos temporadas."""
    from sportstar.backtesting.dataset import HistoricalGame

    train = make_games(n_days=120, season=2011, start=date(2011, 4, 1))
    test = make_games(n_days=120, season=2019, start=date(2019, 4, 1), seed=9)
    games: list[HistoricalGame] = [*train, *test]

    monkeypatch.setattr(run_module, "load", lambda sport: FakeHistory(games))
    monkeypatch.setattr(run_module, "to_historical_games", lambda raw: raw)
    monkeypatch.setattr(
        run_module,
        "temporal_split",
        lambda g: run_module.Split(
            train=[x for x in g if x.season == 2011],
            test=[x for x in g if x.season == 2019],
        ),
    )


def test_por_defecto_el_test_set_no_se_toca(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _patch_history(monkeypatch)
    ledger = HoldoutLedger(tmp_path / "ledger.json")

    assert run_module.run(ledger=ledger) == 0

    out = capsys.readouterr().out
    assert "TRAIN" in out
    assert "test set NO evaluado" in out
    assert "=== TEST" not in out
    assert ledger.uses("mlb_2019_2019") == 0


def test_con_el_flag_se_evalua_y_queda_anotado(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _patch_history(monkeypatch)
    ledger = HoldoutLedger(tmp_path / "ledger.json")

    assert run_module.run(use_test_set=True, ledger=ledger) == 0

    out = capsys.readouterr().out
    assert "=== TEST — uso nº 1" in out
    assert ledger.uses("mlb_2019_2019") == 1


def test_el_segundo_uso_del_test_set_sale_avisado(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """El contador no impide mirar otra vez. Le quita la deniabilidad."""
    _patch_history(monkeypatch)
    ledger = HoldoutLedger(tmp_path / "ledger.json")

    run_module.run(use_test_set=True, ledger=ledger)
    capsys.readouterr()
    run_module.run(use_test_set=True, ledger=ledger)

    out = capsys.readouterr().out
    assert "uso nº 2" in out
    assert "más ancho que el que declara" in out


def test_una_estrategia_bloqueada_sigue_apareciendo_en_la_tabla() -> None:
    """Dejarla fuera convertiría la tabla en un ranking de supervivientes."""
    from sportstar.backtesting.run import _table, blocked
    from sportstar.backtesting.strategies import Elo

    class Oracle:
        name = "oracle"
        version = "v-trampa"

        def predict_home(self, game: object) -> float:
            return 0.95 if game.home_won else 0.05  # type: ignore[attr-defined]

        def observe(self, game: object) -> None:
            pass

    result = run_backtest(make_games(n_days=200), Oracle())  # type: ignore[arg-type]
    row = blocked(result)

    assert row.is_blocked
    assert not row.beats_market
    assert "oracle" in _table([row])
    assert "BLOQUEADO" in _table([row])
    assert isinstance(Elo(), Elo)


def test_el_motivo_del_bloqueo_se_imprime(capsys: pytest.CaptureFixture[str]) -> None:
    from sportstar.backtesting.run import _print_blocked

    row = Comparison(
        strategy="s",
        version="v",
        n=0,
        brier=float("nan"),
        market_brier=float("nan"),
        beat_market_rate=float("nan"),
        z_score=float("nan"),
        n_bets=0,
        roi=float("nan"),
        blocked_by=("roi_vs_sample_size",),
    )

    _print_blocked([row])

    assert "roi_vs_sample_size" in capsys.readouterr().out
