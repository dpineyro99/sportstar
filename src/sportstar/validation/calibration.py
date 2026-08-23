"""Métricas de calibración.

**Por qué la calibración y no el acierto.** Un modelo que acierta el 70% de los
partidos puede perder dinero, y uno que acierta el 53% puede ganarlo. Lo que
decide es si sus probabilidades son *correctas*: cuando dice 60%, ¿gana seis de
cada diez?

Un modelo bien calibrado con poca ventaja es apostable. Uno mal calibrado con
mucha "accuracy" no lo es, porque el tamaño de la apuesta se calcula a partir de
la probabilidad, y una probabilidad inflada produce stakes inflados justo en las
apuestas donde más se equivoca.

Todas las funciones son puras y trabajan sobre listas, sin dependencias
externas: el núcleo de evaluación no debe arrastrar el peso de la capa de
modelado.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

# Recorte para el log loss: log(0) es infinito, y una sola predicción de 0.0 en
# un partido que se ganó haría infinita la métrica de todo el conjunto.
_EPS = 1e-15


def brier_score(probabilities: Sequence[float], outcomes: Sequence[int]) -> float:
    """Error cuadrático medio de las probabilidades. Menor es mejor.

    Es la métrica principal del sistema. Un modelo que siempre dice 0.5 obtiene
    0.25; batir eso es el mínimo exigible para que un modelo exista.
    """
    if len(probabilities) != len(outcomes):
        raise ValueError("probabilidades y resultados deben tener la misma longitud")
    if not probabilities:
        raise ValueError("no se puede evaluar un conjunto vacío")
    return sum((p - y) ** 2 for p, y in zip(probabilities, outcomes, strict=True)) / len(outcomes)


def log_loss(probabilities: Sequence[float], outcomes: Sequence[int]) -> float:
    """Log loss. Castiga la confianza equivocada mucho más que el Brier.

    Útil precisamente por eso: un modelo que dice 95% y falla es peligroso para
    el bankroll de una forma que el Brier suaviza.
    """
    if not probabilities:
        raise ValueError("no se puede evaluar un conjunto vacío")
    total = 0.0
    for p, y in zip(probabilities, outcomes, strict=True):
        clipped = min(max(p, _EPS), 1.0 - _EPS)
        total += -(y * math.log(clipped) + (1 - y) * math.log(1.0 - clipped))
    return total / len(outcomes)


@dataclass(frozen=True, slots=True)
class CalibrationBin:
    """Un tramo de la curva de calibración."""

    lower: float
    upper: float
    count: int
    mean_predicted: float
    observed_rate: float

    @property
    def gap(self) -> float:
        """Cuánto se desvía lo observado de lo prometido en este tramo."""
        return self.observed_rate - self.mean_predicted


def calibration_curve(
    probabilities: Sequence[float], outcomes: Sequence[int], bins: int = 10
) -> list[CalibrationBin]:
    """Agrupa las predicciones y compara lo prometido con lo ocurrido.

    Es el diagnóstico que el Brier resume en un número: dice *dónde* falla el
    modelo. Un sistema que sobreestima solo en los favoritos fuertes tiene un
    problema distinto —y una solución distinta— que uno que falla en todas partes.
    """
    grouped: dict[int, list[tuple[float, int]]] = {}
    for p, y in zip(probabilities, outcomes, strict=True):
        index = min(int(p * bins), bins - 1)
        grouped.setdefault(index, []).append((p, y))

    curve = []
    for index in sorted(grouped):
        pairs = grouped[index]
        curve.append(
            CalibrationBin(
                lower=index / bins,
                upper=(index + 1) / bins,
                count=len(pairs),
                mean_predicted=sum(p for p, _ in pairs) / len(pairs),
                observed_rate=sum(y for _, y in pairs) / len(pairs),
            )
        )
    return curve


def expected_calibration_error(
    probabilities: Sequence[float], outcomes: Sequence[int], bins: int = 10
) -> float:
    """Desviación media entre lo prometido y lo observado, ponderada por tramo.

    Resume la curva en un número comparable entre modelos. 0 es calibración
    perfecta.
    """
    curve = calibration_curve(probabilities, outcomes, bins)
    total = sum(b.count for b in curve)
    return sum(b.count * abs(b.gap) for b in curve) / total if total else 0.0


@dataclass(frozen=True, slots=True)
class CalibrationReport:
    """Evaluación completa de un conjunto de predicciones."""

    n: int
    brier: float
    log_loss: float
    calibration_error: float
    base_rate: float
    mean_prediction: float

    @property
    def brier_skill_vs_base_rate(self) -> float:
        """Mejora relativa sobre predecir siempre la tasa base.

        Positivo significa que el modelo aporta algo sobre "el local gana el 52%
        de las veces". Negativo significa que aporta ruido, y ese es el resultado
        que hay que estar dispuesto a aceptar.
        """
        base_brier = self.base_rate * (1 - self.base_rate)
        return (base_brier - self.brier) / base_brier if base_brier else 0.0


def evaluate(probabilities: Sequence[float], outcomes: Sequence[int]) -> CalibrationReport:
    """Informe completo. Es lo que decide si un modelo se despliega."""
    return CalibrationReport(
        n=len(outcomes),
        brier=brier_score(probabilities, outcomes),
        log_loss=log_loss(probabilities, outcomes),
        calibration_error=expected_calibration_error(probabilities, outcomes),
        base_rate=sum(outcomes) / len(outcomes),
        mean_prediction=sum(probabilities) / len(probabilities),
    )
