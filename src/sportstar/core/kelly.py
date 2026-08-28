"""Dimensionamiento de apuesta: flat y Kelly fraccional.

Kelly maximiza el crecimiento logarítmico del bankroll **asumiendo que
`model_prob` es correcta**. No lo es. La fracción 0.25 por defecto no es una
preferencia de riesgo: es una corrección por error de estimación del modelo.

El cap absoluto existe porque Kelly con una probabilidad mal estimada en un
underdog largo produce stakes absurdos — y ese es exactamente el escenario donde
el modelo es menos fiable. Un stake que Kelly justifica pero que el sistema no
recomienda es el comportamiento correcto, no un bug.

Unidades: `1 unit = 1% del bankroll` (decisión D4), así que un bankroll completo
son 100 units y una fracción de Kelly `f` equivale a `f * 100` units.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .errors import CoreError
from .odds import validate_decimal, validate_probability

DEFAULT_KELLY_FRACTION = 0.25
DEFAULT_MAX_STAKE_UNITS = 5.0
UNITS_PER_BANKROLL = 100.0


class SizingMethod(StrEnum):
    """Se persiste en `recommendations.sizing_method`."""

    FLAT = "flat"
    KELLY_FRACTIONAL = "kelly_fractional"


@dataclass(frozen=True, slots=True)
class StakeConfig:
    """Configuración de dimensionamiento.

    `max_stake_units` es un límite duro sobre la recomendación final, no una
    sugerencia: se aplica después de la fracción de Kelly.
    """

    method: SizingMethod = SizingMethod.KELLY_FRACTIONAL
    kelly_fraction: float = DEFAULT_KELLY_FRACTION
    max_stake_units: float = DEFAULT_MAX_STAKE_UNITS
    flat_stake_units: float = 1.0
    units_per_bankroll: float = UNITS_PER_BANKROLL

    def __post_init__(self) -> None:
        if not 0.0 < self.kelly_fraction <= 1.0:
            raise CoreError(f"kelly_fraction debe estar en (0, 1], recibido {self.kelly_fraction}")
        if self.max_stake_units <= 0.0:
            raise CoreError(f"max_stake_units debe ser > 0, recibido {self.max_stake_units}")
        if self.flat_stake_units <= 0.0:
            raise CoreError(f"flat_stake_units debe ser > 0, recibido {self.flat_stake_units}")


def full_kelly(model_prob: float, decimal_odds: float) -> float:
    """Fracción **del bankroll** que recomienda Kelly completo.

    `f = (p·d - 1) / (d - 1)`. Negativa si la apuesta es -EV; se devuelve tal
    cual para que el llamante decida, en vez de esconder el signo.

    >>> full_kelly(0.55, 2.0)
    0.09999999999999998
    """
    p = validate_probability(model_prob, name="model_prob")
    d = validate_decimal(decimal_odds)
    b = d - 1.0
    return (p * d - 1.0) / b


def fractional_kelly(
    model_prob: float,
    decimal_odds: float,
    fraction: float = DEFAULT_KELLY_FRACTION,
) -> float:
    """Kelly fraccional, recortado a 0 en apuestas -EV."""
    if not 0.0 < fraction <= 1.0:
        raise CoreError(f"fraction debe estar en (0, 1], recibido {fraction}")
    return max(0.0, full_kelly(model_prob, decimal_odds) * fraction)


@dataclass(frozen=True, slots=True)
class Stake:
    """Recomendación de tamaño, con trazabilidad de si se aplicó el cap."""

    units: float
    method: SizingMethod
    full_kelly_fraction: float
    uncapped_units: float
    was_capped: bool


def recommend_stake(
    model_prob: float,
    decimal_odds: float,
    config: StakeConfig | None = None,
) -> Stake:
    """Calcula el stake recomendado en units.

    Una apuesta -EV devuelve 0 units en cualquier método: si el EV es negativo,
    apostar plano tampoco lo arregla.

    `was_capped` se persiste porque un cap frecuente es señal de que el modelo
    está produciendo probabilidades demasiado extremas — un síntoma de mala
    calibración que conviene ver en el dashboard, no enterrar.
    """
    cfg = config or StakeConfig()
    f_full = full_kelly(model_prob, decimal_odds)

    if f_full <= 0.0:
        return Stake(0.0, cfg.method, f_full, 0.0, was_capped=False)

    if cfg.method is SizingMethod.FLAT:
        uncapped = cfg.flat_stake_units
    else:
        uncapped = f_full * cfg.kelly_fraction * cfg.units_per_bankroll

    units = min(uncapped, cfg.max_stake_units)
    return Stake(
        units=units,
        method=cfg.method,
        full_kelly_fraction=f_full,
        uncapped_units=uncapped,
        was_capped=units < uncapped,
    )
