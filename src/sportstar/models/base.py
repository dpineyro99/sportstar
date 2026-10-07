"""Interfaz común de los modelos.

Un `SportModel` produce **probabilidades con incertidumbre**, no picks. El pick
es una consecuencia posterior de comparar esa probabilidad con un precio, y vive
en el pipeline de candidates, no aquí.

Añadir un deporte no debe requerir tocar nada aguas abajo de esta interfaz. Si
en algún punto del núcleo aparece un `if sport == "MLB"`, el punto de extensión
está mal diseñado.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from ..core.odds import validate_probability


@dataclass(frozen=True, slots=True)
class ModelPrediction:
    """Probabilidad de una selección, con su incertidumbre y su linaje.

    `lower`/`upper` no son decorativos: el Confidence Score mide el edge en
    desviaciones típicas, no en puntos porcentuales. Un edge de 3 puntos con un
    modelo muy incierto no es la misma apuesta que 3 puntos con uno estrecho, y
    sin el intervalo esa diferencia es invisible.
    """

    selection_id: int
    probability: float
    lower: float | None
    upper: float | None
    model_name: str
    model_version: str
    as_of: datetime

    def __post_init__(self) -> None:
        validate_probability(self.probability, name="probability")
        if self.lower is not None and self.upper is not None and self.lower > self.upper:
            raise ValueError(f"intervalo invertido: [{self.lower}, {self.upper}]")

    @property
    def uncertainty(self) -> float | None:
        """Semiancho del intervalo. `None` si el modelo no lo reporta."""
        if self.lower is None or self.upper is None:
            return None
        return (self.upper - self.lower) / 2.0


@runtime_checkable
class SportModel(Protocol):
    """Contrato de un modelo. Deliberadamente mínimo."""

    name: str
    version: str

    def predict(self, context: object, as_of: datetime) -> dict[int, ModelPrediction]:
        """Probabilidades por `selection_id`.

        Solo puede usar información disponible estrictamente antes de `as_of`.
        El invariante lo verifica `validation/sanity.py`; aquí queda declarado.
        """
        ...
