"""Diagnóstico de conjuntos de features.

Existe por un fallo real: un modelo con cinco features de MLB salió con **cuatro
coeficientes de signo invertido** —mejor Elo predecía *menos* victorias— mientras
las métricas parecían razonables.

La causa era colinealidad. `elo_diff`, `season_win_pct_diff` y
`venue_win_pct_diff` correlacionaban entre 0.82 y 0.93: no eran cinco señales,
era una señal medida cinco veces. Cuando las columnas dicen lo mismo, la
regresión reparte el peso de forma arbitraria y los signos se vuelven ruido.

Lo grave no es la métrica —apenas se movía— sino que **las explicaciones se
vuelven mentira**. Este sistema muestra al usuario "descanso: -0.07%" derivado
del coeficiente, y un coeficiente invertido convierte esa frase en desinformación
con formato de dato. Prefiero un modelo peor y explicable a uno igual y opaco.

Estos checks corren antes de aceptar un modelo. Son baratos y atrapan un fallo
que las métricas agregadas esconden.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

# Por encima de esto, dos features son la misma medida con otro nombre.
COLLINEARITY_THRESHOLD = 0.80
# Correlación mínima con el resultado para que una feature merezca estar.
MIN_SIGNAL = 0.02


def correlation(a: Sequence[float], b: Sequence[float]) -> float:
    """Correlación de Pearson. 0.0 si alguna serie es constante."""
    if len(a) != len(b):
        raise ValueError("las series deben tener la misma longitud")
    if len(a) < 2:
        return 0.0
    mean_a, mean_b = statistics.mean(a), statistics.mean(b)
    numerator = sum((x - mean_a) * (y - mean_b) for x, y in zip(a, b, strict=True))
    denominator = math.sqrt(sum((x - mean_a) ** 2 for x in a) * sum((y - mean_b) ** 2 for y in b))
    return numerator / denominator if denominator else 0.0


@dataclass(frozen=True, slots=True)
class CollinearPair:
    left: str
    right: str
    correlation: float


def find_collinear_pairs(
    columns: Mapping[str, Sequence[float]], threshold: float = COLLINEARITY_THRESHOLD
) -> list[CollinearPair]:
    """Pares de features que miden lo mismo, de mayor a menor correlación."""
    names = sorted(columns)
    pairs = [
        CollinearPair(left, right, value)
        for i, left in enumerate(names)
        for right in names[i + 1 :]
        if abs(value := correlation(columns[left], columns[right])) >= threshold
    ]
    return sorted(pairs, key=lambda p: -abs(p.correlation))


@dataclass(frozen=True, slots=True)
class SignFlip:
    """Una feature cuyo coeficiente contradice su relación con el resultado."""

    feature: str
    marginal_correlation: float
    coefficient: float

    @property
    def message(self) -> str:
        return (
            f"{self.feature}: correlaciona {self.marginal_correlation:+.3f} con el "
            f"resultado pero su coeficiente es {self.coefficient:+.3f}. La "
            f"explicación que genere sobre este factor dirá lo contrario de lo "
            f"que muestran los datos."
        )


def find_sign_flips(
    columns: Mapping[str, Sequence[float]],
    outcomes: Sequence[float],
    coefficients: Mapping[str, float],
    *,
    min_signal: float = MIN_SIGNAL,
) -> list[SignFlip]:
    """Features cuyo coeficiente contradice su correlación con el resultado.

    Se ignoran las features casi sin señal: si una feature apenas correlaciona
    con el resultado, el signo de su coeficiente es ruido y marcarlo sería una
    falsa alarma. Lo que importa es la contradicción **con evidencia detrás**.
    """
    flips = []
    for name, coefficient in coefficients.items():
        if name not in columns:
            continue
        marginal = correlation(columns[name], outcomes)
        if abs(marginal) < min_signal:
            continue
        if marginal * coefficient < 0:
            flips.append(SignFlip(name, marginal, coefficient))
    return flips


@dataclass(frozen=True, slots=True)
class FeatureDiagnostics:
    """Veredicto sobre un conjunto de features y su modelo."""

    collinear: list[CollinearPair]
    sign_flips: list[SignFlip]
    weak: list[str]

    @property
    def is_interpretable(self) -> bool:
        """False si algún coeficiente contradice los datos.

        Un modelo no interpretable puede desplegarse igualmente si sus métricas
        lo justifican — pero **no puede generar explicaciones**, porque las que
        generaría serían falsas.
        """
        return not self.sign_flips

    def render(self) -> str:
        if not (self.collinear or self.sign_flips or self.weak):
            return "FEATURES  ok  (sin colinealidad ni signos invertidos)"

        lines = ["FEATURES  revisar"]
        for pair in self.collinear:
            lines.append(
                f"  [COLINEAL] {pair.left} ~ {pair.right}: r={pair.correlation:+.2f}. "
                "Miden lo mismo; el reparto de peso entre ambas es arbitrario."
            )
        for flip in self.sign_flips:
            lines.append(f"  [SIGNO]    {flip.message}")
        if self.weak:
            lines.append(f"  [DÉBIL]    sin señal apreciable: {', '.join(self.weak)}")
        return "\n".join(lines)


def diagnose(
    columns: Mapping[str, Sequence[float]],
    outcomes: Sequence[float],
    coefficients: Mapping[str, float] | None = None,
    *,
    threshold: float = COLLINEARITY_THRESHOLD,
    min_signal: float = MIN_SIGNAL,
) -> FeatureDiagnostics:
    """Diagnóstico completo. Se ejecuta antes de aceptar un modelo."""
    return FeatureDiagnostics(
        collinear=find_collinear_pairs(columns, threshold),
        sign_flips=(
            find_sign_flips(columns, outcomes, coefficients, min_signal=min_signal)
            if coefficients
            else []
        ),
        weak=[
            name
            for name in sorted(columns)
            if abs(correlation(columns[name], outcomes)) < min_signal
        ],
    )
