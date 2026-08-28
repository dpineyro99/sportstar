"""Closing Line Value.

El closing line de un book sharp es una de las predicciones mejor calibradas que
existen en cualquier dominio. Batirlo de forma sistemática es la señal más
temprana y fiable de que una estrategia tiene ventaja real.

**Por qué es la métrica central.** El P&L necesita una muestra que tarda años en
acumularse; el CLV es 8-10x más eficiente:

| Qué demostrar                     | Muestra necesaria |
|-----------------------------------|-------------------|
| Beat-close rate real del 55%      | ~500-1.000        |
| ROI real del +3%                  | ~5.000-8.000      |

Aun así, ~500 apuestas son una temporada entera a 2-3 apuestas diarias. Por eso
el sistema mide CLV sobre **todos los candidates**, no solo sobre lo apostado
(ARCHITECTURE.md §4.6): la misma señal con uno o dos órdenes de magnitud más de
muestra, sin arriesgar nada.
"""

from __future__ import annotations

from dataclasses import dataclass

from .odds import validate_decimal, validate_probability


def clv_price(taken_decimal: float, closing_decimal: float) -> float:
    """CLV de precio: `taken/closing - 1`. `0.023` = conseguiste un 2.3% mejor precio.

    Mide el precio realmente tomado en el book donde se apostó, frente al cierre
    de ese mismo mercado.
    """
    taken = validate_decimal(taken_decimal)
    closing = validate_decimal(closing_decimal)
    return taken / closing - 1.0


def clv_probability(fair_prob_at_bet: float, closing_fair_prob: float) -> float:
    """CLV de probabilidad: `closing_fair - fair_at_bet`, ambos SIN vig.

    Positivo cuando el mercado se movió hacia nuestro lado. Es la versión
    comparable entre mercados y deportes, porque no depende del vig del book en
    el que se ejecutó.
    """
    at_bet = validate_probability(fair_prob_at_bet, name="fair_prob_at_bet")
    closing = validate_probability(closing_fair_prob, name="closing_fair_prob")
    return closing - at_bet


def beat_closing_line(taken_decimal: float, closing_decimal: float) -> bool:
    """True si el precio tomado es mejor que el de cierre."""
    return validate_decimal(taken_decimal) > validate_decimal(closing_decimal)


def model_beat_close(
    model_prob: float,
    market_fair_prob_at_eval: float,
    closing_fair_prob: float,
) -> bool:
    """¿Estaba el modelo más cerca del cierre que el propio mercado en su momento?

    Esta es la métrica de ARCHITECTURE.md §4.6 y **no requiere apostar**: se puede
    evaluar sobre cada selección de cada evento, apostada o no. Es lo que hace
    viable validar un modelo en semanas en vez de en temporadas.

    Un empate exacto cuenta como no-superado: ante la duda, no acreditamos
    ventaja al modelo.
    """
    p = validate_probability(model_prob, name="model_prob")
    q = validate_probability(market_fair_prob_at_eval, name="market_fair_prob_at_eval")
    close = validate_probability(closing_fair_prob, name="closing_fair_prob")
    return abs(p - close) < abs(q - close)


@dataclass(frozen=True, slots=True)
class ClvResult:
    """CLV completo de una apuesta liquidada."""

    clv_price: float
    clv_probability: float
    beat_closing_line: bool


def evaluate_clv(
    *,
    taken_decimal: float,
    closing_decimal: float,
    fair_prob_at_bet: float,
    closing_fair_prob: float,
) -> ClvResult:
    """Calcula ambas variantes de CLV para una apuesta."""
    return ClvResult(
        clv_price=clv_price(taken_decimal, closing_decimal),
        clv_probability=clv_probability(fair_prob_at_bet, closing_fair_prob),
        beat_closing_line=beat_closing_line(taken_decimal, closing_decimal),
    )
