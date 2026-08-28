"""Contrato de las features: point-in-time o nada.

La regla que gobierna todo el paquete: **una feature con `as_of = T` solo puede
derivarse de hechos que ya conocíamos estrictamente antes de T.**

No es "conviene evitar el leakage". Es que el leakage no produce un error, produce
resultados *mejores*. Un backtest con features contaminadas sale precioso,
convence, y no se reproduce en paper trading. Para cuando se nota, se han perdido
meses.

Por eso el invariante no se confía a la disciplina:

- `FeatureVector` guarda su `as_of` y la ventana de datos que consumió.
- `assert_point_in_time` compara ambas cosas y **lanza** si se cruzan.
- El criterio no es la fecha del hecho sino `observed_at`: cuándo estuvo
  disponible **para nosotros**. Un marcador corregido dos días después no
  estaba ahí el día del partido, por mucho que su fecha diga lo contrario.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable


class LeakageError(RuntimeError):
    """Una feature usó información que no estaba disponible en su `as_of`."""


@dataclass(frozen=True, slots=True)
class Observation:
    """Un hecho con la fecha en que lo supimos.

    `observed_at` no es decorativo ni redundante con la fecha del hecho: es la
    única de las dos que el backtest puede usar sin mentir.
    """

    observed_at: datetime
    payload: Any = None


@dataclass(frozen=True, slots=True)
class FeatureVector:
    """Features de una entidad en un instante, con su procedencia."""

    entity_id: int
    as_of: datetime
    values: dict[str, float]
    # El hecho más reciente que entró en el cálculo. Es lo que permite verificar
    # el invariante después, sin volver a recorrer los datos de origen.
    latest_observation_at: datetime | None = None
    sample_size: int = 0
    missing: tuple[str, ...] = ()

    @property
    def is_complete(self) -> bool:
        return not self.missing

    def get(self, key: str, default: float = 0.0) -> float:
        return self.values.get(key, default)


def assert_point_in_time(vector: FeatureVector) -> None:
    """Verifica el invariante. Lanza `LeakageError` si se viola.

    Estrictamente anterior: un hecho observado en el mismo instante del corte no
    estaba disponible *antes* de él, y en la práctica esa igualdad casi siempre
    delata un `as_of` derivado del propio dato.
    """
    latest = vector.latest_observation_at
    if latest is not None and latest >= vector.as_of:
        raise LeakageError(
            f"entidad {vector.entity_id}: feature con as_of={vector.as_of.isoformat()} "
            f"derivada de un hecho observado en {latest.isoformat()}. "
            "El backtest que use esto no significará nada."
        )


def filter_available(observations: list[Observation], as_of: datetime) -> list[Observation]:
    """Recorta a lo que ya se sabía antes de `as_of`.

    Toda feature debe pasar sus datos por aquí. Es una línea, y es la diferencia
    entre un backtest reproducible y uno que miente.
    """
    return [o for o in observations if o.observed_at < as_of]


@runtime_checkable
class FeatureBuilder(Protocol):
    """Un builder por deporte. El core nunca sabe de qué deporte se trata."""

    name: str
    version: str
    feature_names: tuple[str, ...]

    def build(self, entity_id: int, as_of: datetime) -> FeatureVector:
        """Features de una entidad en un instante.

        Solo puede leer hechos con `observed_at < as_of`.
        """
        ...


@dataclass
class FeatureSpec:
    """Descripción versionada de un conjunto de features.

    Se persiste en `feature_sets.spec`. Sin esto, un cambio en cómo se calcula
    una feature deja las filas antiguas atribuidas a una definición que ya no
    existe, y el histórico deja de ser comparable consigo mismo.
    """

    name: str
    version: str
    features: tuple[str, ...]
    description: str = ""
    params: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "features": list(self.features),
            "description": self.description,
            "params": self.params,
        }
