"""Edge y expected value.

Las tres probabilidades que nunca deben confundirse (ARCHITECTURE.md §4.2):

| Concepto              | Origen                                   | Uso                |
|-----------------------|------------------------------------------|--------------------|
| `implied_prob`        | 1/decimal del precio, CON vig            | break-even         |
| `market_fair_prob`    | no-vig del CONSENSO SHARP                | referencia de edge |
| `model_prob`          | salida del modelo                        | numerador del edge |

Y dos precios distintos:

- **precio de referencia** — el sharp. Define la probabilidad justa.
- **precio ejecutable** — el mejor disponible donde realmente puedes apostar.
  Define el EV.

El edge se mide contra la mejor estimación del mercado; el EV se calcula con el
precio que puedes conseguir. Usar el mismo precio para ambos convierte el
sistema en un detector de su propio ruido.
"""

from __future__ import annotations

from dataclasses import dataclass

from .odds import decimal_to_implied, validate_decimal, validate_probability


def edge(model_prob: float, market_fair_prob: float) -> float:
    """`model_prob - market_fair_prob`, en puntos de probabilidad (0.052 = +5.2%).

    `market_fair_prob` debe venir de `novig.remove_vig`. Pasarle una implied con
    vig infla el edge de forma sistemática — el error más común del dominio.
    """
    p = validate_probability(model_prob, name="model_prob")
    q = validate_probability(market_fair_prob, name="market_fair_prob")
    return p - q


def structural_edge(market_fair_prob: float, best_available_decimal: float) -> float:
    """Edge disponible **sin modelo alguno** (ARCHITECTURE.md §1.1).

    Es `fair_prob_sharp - implied_prob_best_book`: line shopping formalizado.
    Positivo en expectativa cuando el mejor precio recreativo supera al consenso
    sharp. Establece el suelo del sistema — cualquier modelo estadístico debe
    justificarse mejorando este número, no simplemente siendo positivo.
    """
    q = validate_probability(market_fair_prob, name="market_fair_prob")
    return q - decimal_to_implied(best_available_decimal)


def total_edge(model_prob: float, best_available_decimal: float) -> float:
    """Ventaja total sobre el precio que realmente vas a pagar.

    Descompone exactamente en las dos fuentes de ventaja del sistema:

        total_edge = (model_prob - fair)  +  (fair - implied_best)
                   =      edge            +   structural_edge

    Es la magnitud que decide si una apuesta es +EV: `total_edge > 0` si y solo
    si `expected_value > 0`. Por eso es la que filtran los gates, mientras que
    `edge` y `structural_edge` se conservan por separado para poder atribuir de
    dónde vino la ventaja.

    La distinción no es académica. `market_consensus_v1` tiene `edge = 0` por
    construcción —copia al mercado— y aun así produce apuestas rentables vía
    `structural_edge`. Filtrar por `edge` dejaría al baseline de mercado sin
    recomendar nada, que es justo lo que Phase 2a necesita medir.
    """
    p = validate_probability(model_prob, name="model_prob")
    return p - decimal_to_implied(best_available_decimal)


def expected_value(model_prob: float, decimal_odds: float) -> float:
    """EV por unidad apostada. `0.084` = +8.4% de ROI esperado.

    `p·(d-1) - (1-p)`: ganancia neta si acierta, pérdida de la unidad si falla.
    Ya está normalizado a stake = 1, así que coincide con el ROI esperado.

    Se calcula con el **precio ejecutable**, no con el de referencia.
    """
    p = validate_probability(model_prob, name="model_prob")
    d = validate_decimal(decimal_odds)
    return p * (d - 1.0) - (1.0 - p)


def expected_roi(model_prob: float, decimal_odds: float) -> float:
    """Alias explícito de `expected_value`, que ya está por unidad de stake.

    Existe para que el dashboard nunca muestre "EV: 0.084" sin unidad y alguien
    lo lea como dólares.
    """
    return expected_value(model_prob, decimal_odds)


def breakeven_model_prob(decimal_odds: float) -> float:
    """Probabilidad a partir de la cual la apuesta deja de ser negativa."""
    return decimal_to_implied(decimal_odds)


@dataclass(frozen=True, slots=True)
class EdgeBreakdown:
    """Resultado completo de evaluar una selección contra el mercado.

    Frozen a propósito: una evaluación es un hecho fechado. Si cambia el precio
    se construye otra, no se muta esta.
    """

    model_prob: float
    market_implied_prob: float
    market_fair_prob: float
    reference_decimal: float
    best_decimal: float
    edge: float
    structural_edge: float
    total_edge: float
    expected_value: float
    break_even_prob: float

    @property
    def is_positive_ev(self) -> bool:
        return self.expected_value > 0.0


def evaluate(
    *,
    model_prob: float,
    market_fair_prob: float,
    reference_decimal: float,
    best_decimal: float | None = None,
    market_implied_prob: float | None = None,
) -> EdgeBreakdown:
    """Evalúa una selección. `best_decimal` cae al de referencia si no se pasa.

    Que `best_decimal` sea opcional refleja el caso real de un book único; que
    sea un parámetro separado impide que el caso general los confunda.

    `market_implied_prob` debe pasarse cuando la referencia es un **consenso**:
    ahí no existe un precio único con vig, y derivarla de `reference_decimal`
    devolvería la propia fair probability redicha. Con un solo book de referencia
    sí se puede derivar, y por eso es opcional.
    """
    best = validate_decimal(best_decimal if best_decimal is not None else reference_decimal)
    ref = validate_decimal(reference_decimal)
    implied = (
        validate_probability(market_implied_prob, name="market_implied_prob")
        if market_implied_prob is not None
        else decimal_to_implied(ref)
    )
    return EdgeBreakdown(
        model_prob=validate_probability(model_prob, name="model_prob"),
        market_implied_prob=implied,
        market_fair_prob=validate_probability(market_fair_prob, name="market_fair_prob"),
        reference_decimal=ref,
        best_decimal=best,
        edge=edge(model_prob, market_fair_prob),
        structural_edge=structural_edge(market_fair_prob, best),
        total_edge=total_edge(model_prob, best),
        expected_value=expected_value(model_prob, best),
        break_even_prob=decimal_to_implied(best),
    )
