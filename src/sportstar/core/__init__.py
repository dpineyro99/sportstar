"""Núcleo matemático: funciones puras, sin I/O, sin dependencia de deporte.

Es la única parte del sistema donde un bug no lanza excepción: simplemente
contamina en silencio cada número aguas abajo. De ahí la exigencia de cobertura
y de tests deterministas calculados a mano.
"""

from .clv import (
    ClvResult,
    beat_closing_line,
    clv_price,
    clv_probability,
    evaluate_clv,
    model_beat_close,
)
from .edge import EdgeBreakdown, edge, evaluate, expected_roi, expected_value, structural_edge
from .errors import CoreError, InvalidMarketError, InvalidOddsError, InvalidProbabilityError
from .kelly import SizingMethod, Stake, StakeConfig, fractional_kelly, full_kelly, recommend_stake
from .novig import NoVigMethod, remove_vig, shin_z
from .odds import (
    american_to_decimal,
    american_to_implied,
    break_even_probability,
    decimal_to_american,
    decimal_to_implied,
    implied_to_american,
    implied_to_decimal,
    overround,
    vig,
)

__all__ = [
    "ClvResult",
    "CoreError",
    "EdgeBreakdown",
    "InvalidMarketError",
    "InvalidOddsError",
    "InvalidProbabilityError",
    "NoVigMethod",
    "SizingMethod",
    "Stake",
    "StakeConfig",
    "american_to_decimal",
    "american_to_implied",
    "beat_closing_line",
    "break_even_probability",
    "clv_price",
    "clv_probability",
    "decimal_to_american",
    "decimal_to_implied",
    "edge",
    "evaluate",
    "evaluate_clv",
    "expected_roi",
    "expected_value",
    "fractional_kelly",
    "full_kelly",
    "implied_to_american",
    "implied_to_decimal",
    "model_beat_close",
    "overround",
    "recommend_stake",
    "remove_vig",
    "shin_z",
    "structural_edge",
    "vig",
]
