"""Comando `sportstar backtest`: la comparación completa, en el orden correcto.

El orden no es cosmético. Primero se compara todo sobre **train**, se decide, y
solo entonces —si hay algo que promover— se mira **test**, una vez, anotándolo en
el ledger. Mirar test antes de decidir convierte test en train, y el precio es
que ya no queda ningún conjunto con el que estimar honestamente el error.

Por eso `--test` es un flag explícito y no el comportamiento por defecto: tocar
el test set tiene que ser un acto deliberado que alguien escribe, no algo que
pasa por ejecutar el comando de siempre.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..odds_history import load
from .dataset import HistoricalGame, to_historical_games
from .engine import BacktestResult, run_backtest
from .splits import HoldoutLedger, Split, temporal_split
from .strategies import Elo, EloBlend, MarketConsensus, Strategy


def default_strategies() -> list[Strategy]:
    """Las estrategias que se comparan. `market_consensus` es el listón."""
    return [
        MarketConsensus(),
        Elo(),
        EloBlend(0.05),
        EloBlend(0.10),
        EloBlend(0.20),
    ]


@dataclass(frozen=True, slots=True)
class Comparison:
    """Una fila de la tabla comparativa, con su significación.

    `blocked_by` no es opcional por comodidad: una estrategia que no pasa los
    sanity checks tiene que **seguir apareciendo** en la tabla, marcada. Dejarla
    fuera en silencio convierte la tabla en un ranking de las estrategias que
    sobrevivieron, que es justo la selección que uno no quiere hacer sin verla.
    """

    strategy: str
    version: str
    n: int
    brier: float
    market_brier: float
    beat_market_rate: float
    z_score: float
    n_bets: int
    roi: float
    blocked_by: tuple[str, ...] = ()

    @property
    def is_blocked(self) -> bool:
        return bool(self.blocked_by)

    @property
    def brier_vs_market(self) -> float:
        return self.market_brier - self.brier

    @property
    def beats_market(self) -> bool:
        """Criterio de despliegue: mejor Brier **y** más cerca del cierre.

        Las dos condiciones, no una. Un modelo puede mejorar el Brier por estar
        mejor calibrado sin aportar información nueva, y puede acercarse al cierre
        por ruido. Exigir ambas a la vez es lo que separa señal de casualidad.
        """
        return not self.is_blocked and self.brier_vs_market > 0.0 and self.beat_market_rate > 0.5


def blocked(result: BacktestResult) -> Comparison:
    """Fila para una estrategia bloqueada: se nombra, no se puntúa."""
    return Comparison(
        strategy=result.strategy,
        version=result.strategy_version,
        n=0,
        brier=float("nan"),
        market_brier=float("nan"),
        beat_market_rate=float("nan"),
        z_score=float("nan"),
        n_bets=0,
        roi=float("nan"),
        blocked_by=tuple(f.check for f in result.sanity.blocking),
    )


def compare(result: BacktestResult) -> Comparison:
    model, betting = result.model, result.betting
    # Error típico de una proporción bajo H0: p = 0,5.
    se = 0.5 / math.sqrt(model.n) if model.n else float("inf")
    return Comparison(
        strategy=result.strategy,
        version=result.strategy_version,
        n=model.n,
        brier=model.calibration.brier,
        market_brier=model.market_calibration.brier,
        beat_market_rate=model.beat_market_rate,
        z_score=(model.beat_market_rate - 0.5) / se if se else 0.0,
        n_bets=betting.n_bets,
        roi=betting.roi,
    )


def _table(rows: list[Comparison]) -> str:
    header = (
        f"{'estrategia':<26}{'n':>7}{'Brier':>10}{'vs mercado':>12}"
        f"{'cerca cierre':>14}{'z':>8}{'apuestas':>10}{'ROI':>9}"
    )
    lines = [header, "-" * len(header)]
    for row in rows:
        name = f"{row.strategy} {row.version}"
        if row.is_blocked:
            lines.append(f"{name:<26}{'BLOQUEADO: ' + ', '.join(row.blocked_by):>70}")
            continue
        lines.append(
            f"{name:<26}{row.n:>7}{row.brier:>10.5f}{row.brier_vs_market:>+12.5f}"
            f"{row.beat_market_rate:>14.4f}{row.z_score:>+8.1f}{row.n_bets:>10}{row.roi:>+9.4f}"
        )
    return "\n".join(lines)


def _evaluate(games: list[HistoricalGame], strategies: list[Strategy]) -> list[Comparison]:
    rows = []
    for strategy in strategies:
        result = run_backtest(games, strategy)
        rows.append(compare(result) if result.passed_sanity else blocked(result))
    return rows


def run(use_test_set: bool = False, *, ledger: HoldoutLedger | None = None) -> int:
    """Ejecuta la comparación. Con `use_test_set`, además mide en test una vez."""
    history = load("mlb")
    games = to_historical_games(history.games)
    split: Split = temporal_split(games)

    print(f"partidos utilizables: {len(games)}")
    print(
        f"train {split.train_seasons[0]}-{split.train_seasons[-1]} "
        f"({len(split.train)})   "
        f"test {split.test_seasons[0]}-{split.test_seasons[-1]} ({len(split.test)})"
    )
    print()
    print("=== TRAIN — aquí se itera libremente ===")
    train_rows = _evaluate(split.train, default_strategies())
    print(_table(train_rows))
    _print_blocked(train_rows)
    print()

    promotable = [r for r in train_rows if r.beats_market]
    if promotable:
        print("candidatos a promover (baten al mercado en train):")
        for row in promotable:
            print(f"  {row.strategy} {row.version}")
    else:
        print(
            "ningún modelo bate al mercado en train. No hay nada que promover, y\n"
            "mirar el test set no cambiaría esa conclusión: la decisión ya está tomada."
        )
    print()

    if not use_test_set:
        print("test set NO evaluado (usa --test para hacerlo, y quedará anotado).")
        return 0

    book = ledger or HoldoutLedger()
    label = f"mlb_{split.test_seasons[0]}_{split.test_seasons[-1]}"
    uses = book.record(label)
    print(f"=== TEST — uso nº {uses} de este conjunto ===")
    warning = book.warning(label)
    if warning:
        print(warning)
    test_rows = _evaluate(split.test, default_strategies())
    print(_table(test_rows))
    _print_blocked(test_rows)
    return 0


def _print_blocked(rows: list[Comparison]) -> None:
    """Explica por qué se bloqueó cada fila. Un `BLOQUEADO` sin motivo no informa."""
    for row in rows:
        if row.is_blocked:
            print(f"  {row.strategy} {row.version} bloqueada por: {', '.join(row.blocked_by)}")
