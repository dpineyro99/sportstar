"""Explicaciones de una recomendación, derivadas de números reales.

Regla que gobierna este módulo: **nunca se inventa un factor que el modelo no
usó.** Si el modelo no consume lesiones, la explicación no menciona lesiones
aunque el texto quede peor. Una razón inventada que suena bien es peor que
ninguna: convierte una coincidencia en una convicción.

Para `market_consensus_v1` las razones disponibles son exactamente dos, porque
son las dos únicas cosas que el modelo sabe: la descomposición del edge. Cuando
lleguen modelos con features, se añadirán las contribuciones de coeficientes o
SHAP, y estas dos seguirán siendo válidas.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core.edge import EdgeBreakdown
from ..db.enums import ReasonSource

# Contribuciones por debajo de esto no se listan: ordenar ruido por magnitud
# sugiere una precisión que no existe.
MIN_CONTRIBUTION = 0.001


@dataclass(frozen=True, slots=True)
class Reason:
    """Contribución de un factor, en puntos de probabilidad."""

    factor_key: str
    factor_label: str
    contribution: float
    source: ReasonSource


def build_reasons(breakdown: EdgeBreakdown, best_book_name: str | None = None) -> list[Reason]:
    """Descompone la ventaja en sus fuentes reales, ordenadas por magnitud.

    Las dos fuentes son estructuralmente distintas y merecen etiquetas distintas:
    "el mercado se equivoca" y "este book paga de más" son afirmaciones diferentes
    con implicaciones diferentes, y mezclarlas en un solo número borra
    precisamente la información que hace falta para saber si el modelo aporta.
    """
    book = f" ({best_book_name})" if best_book_name else ""
    candidates = [
        Reason(
            factor_key="model_edge",
            factor_label="Discrepancia del modelo con el mercado",
            contribution=breakdown.edge,
            source=ReasonSource.MODEL_COEFFICIENT,
        ),
        Reason(
            factor_key="structural_edge",
            factor_label=f"Mejor precio disponible que el consenso sharp{book}",
            contribution=breakdown.structural_edge,
            source=ReasonSource.MARKET,
        ),
    ]
    significant = [r for r in candidates if abs(r.contribution) >= MIN_CONTRIBUTION]
    return sorted(significant, key=lambda r: abs(r.contribution), reverse=True)
