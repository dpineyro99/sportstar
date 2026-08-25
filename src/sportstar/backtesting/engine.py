"""El motor: encadena replay, sanity checks y métricas — en ese orden.

La regla que define esta fase: **un backtest que dispara un `FATAL` no produce
un número con asterisco, produce un error.** `BacktestResult.model` y
`.betting` lanzan `SanityBlocked` si el informe no pasó. No hay forma de leer las
métricas sin haber pasado los checks, porque la única forma de leerlas es por
esas propiedades.

Es deliberadamente incómodo. Un backtest con leakage o con muestra insuficiente
que además *enseña* su ROI acaba citado en una conversación tres semanas después,
ya sin el asterisco.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..core.kelly import StakeConfig
from ..core.novig import NoVigMethod
from ..validation.sanity import (
    BacktestSample,
    SanityReport,
    run_sanity_checks,
)
from .dataset import HistoricalGame
from .metrics import (
    BettingPerformance,
    Cut,
    ModelPerformance,
    betting_performance,
    cut_by_edge_bucket,
    cut_by_season,
    model_performance,
    sharpe_like,
)
from .replay import Candidate, ReplayResult, replay
from .strategies import Strategy


class SanityBlocked(RuntimeError):
    """El backtest no pasó los checks. Sus métricas no se muestran."""


@dataclass(frozen=True, slots=True)
class BacktestResult:
    """Resultado de un backtest. Las métricas están detrás del sanity gate."""

    strategy: str
    strategy_version: str
    seasons: tuple[int, ...]
    replay_result: ReplayResult
    sanity: SanityReport
    unevaluable_gates: tuple[str, ...] = field(default=())

    @property
    def candidates(self) -> list[Candidate]:
        return self.replay_result.candidates

    @property
    def passed_sanity(self) -> bool:
        return not self.sanity.blocking

    def _guard(self) -> None:
        if self.sanity.blocking:
            detail = "\n".join(f"  [{f.check}] {f.message}" for f in self.sanity.blocking)
            raise SanityBlocked(
                f"el backtest de {self.strategy} {self.strategy_version} no pasa los "
                f"sanity checks, así que no muestra métricas:\n{detail}"
            )

    @property
    def model(self) -> ModelPerformance:
        self._guard()
        return model_performance(self.candidates)

    @property
    def betting(self) -> BettingPerformance:
        self._guard()
        return betting_performance(self.candidates)

    @property
    def sharpe_like(self) -> float | None:
        self._guard()
        bets = [c for c in self.candidates if c.is_recommended]
        return sharpe_like([c.profit_units / c.stake.units for c in bets])

    def by_season(self) -> list[Cut]:
        self._guard()
        return cut_by_season(self.candidates)

    def by_edge_bucket(self) -> list[Cut]:
        self._guard()
        return cut_by_edge_bucket(self.candidates)

    def summary(self) -> str:
        """Informe legible. Si el sanity bloqueó, dice eso y solo eso."""
        header = (
            f"{self.strategy} {self.strategy_version}   "
            f"temporadas {self.seasons[0]}-{self.seasons[-1]}   "
            f"{self.replay_result.games_replayed} partidos"
        )
        if not self.passed_sanity:
            detail = "\n".join(f"  [{f.check}] {f.message}" for f in self.sanity.blocking)
            return f"{header}\n\nBLOQUEADO por sanity checks. Sin métricas.\n{detail}"

        model, betting = self.model, self.betting
        lines = [
            header,
            "",
            "--- modelo (todos los candidates, contra el cierre) ---",
            f"  n                     {model.n}",
            f"  Brier modelo          {model.calibration.brier:.5f}",
            f"  Brier mercado         {model.market_calibration.brier:.5f}",
            f"  mejora sobre mercado  {model.brier_vs_market:+.5f}",
            f"  ECE modelo            {model.calibration.calibration_error:.5f}",
            f"  más cerca del cierre  {model.beat_market_rate:.4f}  (que el mercado)",
            f"  movimiento del cierre {model.mean_model_clv:+.5f}",
            "",
            "--- filtro (solo recomendaciones) ---",
            f"  apuestas              {betting.n_bets}",
        ]
        if betting.n_bets:
            ratio = self.sharpe_like
            lines += [
                f"  record                {betting.wins}-{betting.losses} ({betting.win_rate:.4f})",
                f"  units apostadas       {betting.units_staked:.2f}",
                f"  units ganadas         {betting.units_profit:+.2f}",
                f"  ROI                   {betting.roi:+.4f}",
                f"  cuota media           {betting.avg_decimal_odds:.3f}",
                f"  drawdown máximo       {betting.max_drawdown_units:.2f} units",
                f"  bate al cierre        {betting.beat_close_rate:.4f}",
                f"  CLV medio             {betting.mean_clv:+.5f}",
                f"  ratio señal/ruido     {ratio:.4f}"
                if ratio is not None
                else "  ratio señal/ruido     n/d (muestra insuficiente)",
            ]
        if self.unevaluable_gates:
            lines += [
                "",
                "--- aviso ---",
                "  gates que este histórico NO puede evaluar, asumidos como superados:",
                f"    {', '.join(self.unevaluable_gates)}",
                "  la evaluación del filtro es por tanto PARCIAL: mide un filtro más",
                "  permisivo que el que corre en vivo.",
            ]
        for finding in self.sanity.findings:
            lines.append(f"[{finding.severity.value.upper()}] {finding.check}: {finding.message}")
        return "\n".join(lines)


def run_backtest(
    games: list[HistoricalGame],
    strategy: Strategy,
    *,
    method: NoVigMethod = NoVigMethod.PROPORTIONAL,
    stake_config: StakeConfig | None = None,
) -> BacktestResult:
    """Ejecuta el backtest completo. Nunca devuelve métricas sin auditar."""
    if not games:
        raise ValueError("no hay partidos que replayear")

    result = replay(games, strategy, method=method, stake_config=stake_config)
    candidates = result.candidates
    if not candidates:
        raise ValueError(
            f"{strategy.name} no generó ni un candidate sobre {len(games)} partidos. "
            "Suele significar que la estrategia devuelve None siempre —por ejemplo "
            "un min_games mayor que la temporada—."
        )

    bets = [c for c in candidates if c.is_recommended]
    profits = [c.profit_units for c in bets]
    staked = sum(c.stake.units for c in bets)
    home = [c for c in candidates if c.side == "home"]

    sanity = run_sanity_checks(
        BacktestSample(
            n_bets=len(bets),
            roi=(sum(profits) / staked) if staked else 0.0,
            win_rate=(sum(c.won for c in bets) / len(bets)) if bets else 0.0,
            avg_decimal_odds=(sum(c.taken_decimal for c in bets) / len(bets) if bets else 2.0),
            edges=[c.total_edge for c in candidates],
            # El contrato point-in-time, comprobado por un módulo que no es el
            # que generó los datos: `sanity` no se fía del replay.
            feature_as_of_pairs=[
                (c.as_of, c.latest_input_observed_at)
                for c in candidates
                if c.latest_input_observed_at is not None
            ],
            markets=[
                (
                    (c.game_date, c.home_team, c.away_team, c.archive_sequence),
                    [c.market_fair_prob, 1.0 - c.market_fair_prob],
                )
                for c in home
            ],
            event_keys=[
                (c.game_date, c.home_team, c.away_team, c.archive_sequence, c.side)
                for c in candidates
            ],
            # El archivo trae el cierre de todos los partidos que trae.
            closing_captured=len(candidates),
            closing_total=len(candidates),
        )
    )

    seasons = tuple(sorted({g.season for g in games}))
    return BacktestResult(
        strategy=strategy.name,
        strategy_version=strategy.version,
        seasons=seasons,
        replay_result=result,
        sanity=sanity,
        unevaluable_gates=result.unevaluable_gates,
    )
