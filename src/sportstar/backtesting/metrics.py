"""Métricas de un backtest, con la separación modelo/filtro incorporada.

Dos informes distintos porque son dos preguntas distintas y **dos tamaños de
muestra que difieren en dos órdenes de magnitud**:

- `ModelPerformance` mide el modelo sobre **todos** los candidates, contra el
  cierre. Decenas de miles de observaciones, conclusiones en semanas.
- `BettingPerformance` mide el filtro sobre las **recomendaciones**. Cientos como
  mucho, conclusiones lentas y provisionales.

Mezclarlas produce el error clásico: un ROI espectacular sobre cuarenta apuestas
presentado con la autoridad estadística de las cuarenta mil predicciones que sí
había.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from ..validation.calibration import CalibrationReport, evaluate
from .replay import Candidate


@dataclass(frozen=True, slots=True)
class ModelPerformance:
    """Cómo de bueno es el modelo. Muestra grande, no requiere apostar."""

    n: int
    calibration: CalibrationReport
    market_calibration: CalibrationReport
    #: Fracción de candidates en los que el modelo quedó más cerca del cierre que
    #: el mercado de apertura. 0,50 es "no aporta"; la baseline de mercado da 0
    #: por construcción, porque no puede estar más cerca del cierre que ella misma.
    beat_market_rate: float
    #: Movimiento medio del cierre respecto al modelo. Tiende a 0 en un modelo
    #: calibrado: es un diagnóstico de sesgo, no una acreditación de señal.
    mean_model_clv: float

    @property
    def brier_vs_market(self) -> float:
        """Cuánto mejora el Brier del modelo sobre el del mercado de apertura.

        Positivo = el modelo aporta. Es **la** cifra que decide si un modelo
        merece desplegarse, por encima de cualquier ROI de backtest.
        """
        return self.market_calibration.brier - self.calibration.brier


@dataclass(frozen=True, slots=True)
class BettingPerformance:
    """Cómo le fue al filtro. Muestra pequeña: conclusiones provisionales."""

    n_bets: int
    wins: int
    losses: int
    units_staked: float
    units_profit: float
    avg_decimal_odds: float
    max_drawdown_units: float
    beat_close_rate: float
    mean_clv: float

    @property
    def roi(self) -> float:
        return self.units_profit / self.units_staked if self.units_staked else 0.0

    @property
    def win_rate(self) -> float:
        return self.wins / self.n_bets if self.n_bets else 0.0


def _drawdown(profits: Sequence[float]) -> float:
    """Máxima caída desde un pico de la curva acumulada, en units."""
    peak = 0.0
    equity = 0.0
    worst = 0.0
    for profit in profits:
        equity += profit
        peak = max(peak, equity)
        worst = min(worst, equity - peak)
    return -worst


def sharpe_like(returns: Sequence[float]) -> float | None:
    """Media sobre desviación típica de los retornos por unidad apostada.

    No es un Sharpe: no hay tasa libre de riesgo ni anualización, y los retornos
    de apuestas no son normales ni independientes. Es un ratio señal/ruido, y se
    llama `sharpe_like` para que nadie lo cite como si fuese lo otro.

    Devuelve `None` con menos de dos apuestas o con varianza cero: un ratio
    calculado sobre una muestra así es un número sin contenido.
    """
    if len(returns) < 2:
        return None
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    if variance <= 0.0:
        return None
    return mean / math.sqrt(variance)


def model_performance(candidates: Sequence[Candidate]) -> ModelPerformance:
    """Evalúa el modelo sobre todos los candidates.

    Solo se usa el lado local: los dos lados de un mismo partido son la misma
    predicción vista del derecho y del revés, y contarlos por separado duplicaría
    artificialmente la muestra sin añadir una sola observación.
    """
    home = [c for c in candidates if c.side == "home"]
    if not home:
        raise ValueError("no hay candidates que evaluar")

    outcomes = [int(c.won) for c in home]
    return ModelPerformance(
        n=len(home),
        calibration=evaluate([c.model_prob for c in home], outcomes),
        market_calibration=evaluate([c.market_fair_prob for c in home], outcomes),
        beat_market_rate=sum(c.model_beat_market for c in home) / len(home),
        mean_model_clv=sum(c.model_clv for c in home) / len(home),
    )


def betting_performance(candidates: Sequence[Candidate]) -> BettingPerformance:
    """Evalúa solo lo que el filtro recomendó."""
    bets = [c for c in candidates if c.is_recommended]
    if not bets:
        return BettingPerformance(0, 0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

    profits = [c.profit_units for c in bets]
    staked = sum(c.stake.units for c in bets)
    return BettingPerformance(
        n_bets=len(bets),
        wins=sum(c.won for c in bets),
        losses=sum(not c.won for c in bets),
        units_staked=staked,
        units_profit=sum(profits),
        avg_decimal_odds=sum(c.taken_decimal for c in bets) / len(bets),
        max_drawdown_units=_drawdown(profits),
        beat_close_rate=sum(c.beat_close for c in bets) / len(bets),
        mean_clv=sum(c.clv for c in bets) / len(bets),
    )


@dataclass(frozen=True, slots=True)
class Cut:
    """Un corte del backtest: un subconjunto con sus dos informes."""

    label: str
    n_candidates: int
    model: ModelPerformance | None
    betting: BettingPerformance


def cut_by_season(candidates: Sequence[Candidate]) -> list[Cut]:
    return _cuts(candidates, lambda c: str(c.season))


def cut_by_edge_bucket(candidates: Sequence[Candidate]) -> list[Cut]:
    """Cortes por tamaño del edge total. Responde "¿qué edge mínimo funciona?"."""

    ranges = ((0.0, 0.01), (0.01, 0.02), (0.02, 0.03), (0.03, 0.05))

    def bucket(candidate: Candidate) -> str:
        edge = candidate.total_edge
        if edge < 0.0:
            return "<0%"
        for low, high in ranges:
            if edge < high:
                return f"{low * 100:.0f}-{high * 100:.0f}%"
        return ">=5%"

    return _cuts(candidates, bucket)


def _cuts(candidates: Sequence[Candidate], key: Callable[[Candidate], str]) -> list[Cut]:
    groups: dict[str, list[Candidate]] = {}
    for candidate in candidates:
        groups.setdefault(key(candidate), []).append(candidate)

    cuts = []
    for label in sorted(groups):
        group = groups[label]
        has_home = any(c.side == "home" for c in group)
        cuts.append(
            Cut(
                label=label,
                n_candidates=len(group),
                model=model_performance(group) if has_home else None,
                betting=betting_performance(group),
            )
        )
    return cuts
