"""Confidence Score 0-10 — versión 0, explícitamente provisional.

**Honestidad sobre qué es esto.** El brief pide un score "no arbitrario". Sin
datos históricos, cualquier fórmula que escribamos hoy *es* arbitraria: no hay
nada contra lo que calibrar los pesos. Lo que sí podemos hacer, y es lo que hace
este módulo, es que sea **explícito, documentado, versionado y auditable** en vez
de un número que aparece sin explicación en el dashboard.

Por eso `CONFIDENCE_VERSION = 0` y por eso se persiste junto a cada
recomendación: cuando Phase 4 dé suficientes resultados para recalibrar los
pesos, se podrá comparar cómo se habría comportado cada versión sobre el mismo
histórico.

El **PAPI SCORE** no se define hasta entonces, por la misma razón y con más
motivo.

## Qué mide y qué no

Mide **cuánta certeza tenemos en la estimación**, no cuán atractiva es la
apuesta. Son cosas distintas y conviene no mezclarlas: se puede tener confianza
alta en que una apuesta es mala. De hecho un candidate claramente -EV suele
puntuar alto, porque la señal es nítida.

Quien decide si se apuesta son los gates de `filters/gates.py`. El confidence
ordena lo que ya pasó ese corte.

## Componentes

| Componente               | Peso | Qué mide                                      |
|--------------------------|------|-----------------------------------------------|
| edge_in_sigmas           | 0.30 | el edge en desviaciones típicas, no en puntos  |
| model_agreement          | 0.20 | dispersión entre modelos / books de referencia |
| data_quality             | 0.15 | features completas, sin datos obsoletos        |
| sample_size              | 0.15 | cuánta historia sostiene las features          |
| line_freshness           | 0.10 | cuán viejo es el precio                        |
| historical_calibration   | 0.10 | acierto pasado en ese bucket de edge           |

## Componentes ausentes

Un componente que no se puede medir aporta **0.5 (neutro)**, no se excluye del
promedio. Excluirlo y renormalizar haría que una apuesta sobre la que sabemos
menos puntuase *más alto* — exactamente al revés de lo que debe pasar.

El neutro tampoco es gratis: `missing_components` viaja en el resultado, y los
gates de `filters/gates.py` exigen por separado que los componentes críticos
estén presentes. Así se separa "¿cómo de buena es esta apuesta?" de "¿sabemos lo
suficiente como para opinar?", que son preguntas distintas.
"""

from __future__ import annotations

from dataclasses import dataclass, field

CONFIDENCE_VERSION = 0

WEIGHTS: dict[str, float] = {
    "edge_in_sigmas": 0.30,
    "model_agreement": 0.20,
    "data_quality": 0.15,
    "sample_size": 0.15,
    "line_freshness": 0.10,
    "historical_calibration": 0.10,
}

NEUTRAL = 0.5

# Un edge de 3 desviaciones típicas satura el componente. El 3 es una convención
# explícita, no un resultado empírico: se revisará en Phase 4 con datos.
EDGE_SIGMA_SATURATION = 3.0
# Historia suficiente para que las features de un equipo dejen de ser ruido.
SAMPLE_SIZE_SATURATION = 40
# Un precio de más de 10 minutos ya no describe el mercado actual.
MAX_LINE_AGE_SECONDS = 600.0


def _clip(value: float) -> float:
    return max(0.0, min(1.0, value))


def score_edge_in_sigmas(edge: float, uncertainty: float | None) -> float | None:
    """Edge relativo a la incertidumbre del modelo.

    Tres puntos de edge con un modelo muy incierto no valen lo mismo que tres
    puntos con uno estrecho. Medirlo en puntos porcentuales, como hace casi todo
    el mundo, borra esa diferencia.
    """
    if uncertainty is None or uncertainty <= 0.0:
        return None
    return _clip(abs(edge) / uncertainty / EDGE_SIGMA_SATURATION)


def score_model_agreement(dispersion: float | None, edge: float) -> float | None:
    """Acuerdo entre fuentes, relativo al tamaño del edge.

    Una dispersión de 1 punto es irrelevante frente a un edge de 6 y demoledora
    frente a uno de 1.5. Por eso se compara con el edge en vez de con una
    constante.
    """
    if dispersion is None:
        return None
    if dispersion <= 0.0:
        return 1.0
    return _clip(1.0 - dispersion / max(abs(edge), 1e-9))


def score_sample_size(observations: int | None) -> float | None:
    """Cuánta historia sostiene las features."""
    if observations is None:
        return None
    return _clip(observations / SAMPLE_SIZE_SATURATION)


def score_line_freshness(age_seconds: float | None) -> float | None:
    """Frescura del precio, lineal hasta el máximo tolerado."""
    if age_seconds is None:
        return None
    return _clip(1.0 - age_seconds / MAX_LINE_AGE_SECONDS)


@dataclass(frozen=True, slots=True)
class ConfidenceResult:
    """Score y su desglose. El desglose se persiste: un número sin explicación
    no se puede auditar ni recalibrar."""

    score: float  # 0-10
    version: int
    components: dict[str, float] = field(default_factory=dict)
    missing_components: tuple[str, ...] = ()

    @property
    def is_fully_informed(self) -> bool:
        return not self.missing_components


def compute_confidence(
    *,
    edge: float,
    uncertainty: float | None = None,
    dispersion: float | None = None,
    data_quality: float | None = None,
    sample_size: int | None = None,
    line_age_seconds: float | None = None,
    historical_calibration: float | None = None,
) -> ConfidenceResult:
    """Calcula el Confidence Score 0-10.

    Se devuelve redondeado a un decimal. Más precisión sería falsa: los pesos no
    están calibrados y mostrar `8.37` sugiere una exactitud que no existe.
    """
    raw: dict[str, float | None] = {
        "edge_in_sigmas": score_edge_in_sigmas(edge, uncertainty),
        "model_agreement": score_model_agreement(dispersion, edge),
        "data_quality": None if data_quality is None else _clip(data_quality),
        "sample_size": score_sample_size(sample_size),
        "line_freshness": score_line_freshness(line_age_seconds),
        "historical_calibration": (
            None if historical_calibration is None else _clip(historical_calibration)
        ),
    }

    missing = tuple(name for name, value in raw.items() if value is None)
    components = {name: (NEUTRAL if value is None else value) for name, value in raw.items()}
    total = sum(components[name] * weight for name, weight in WEIGHTS.items())

    return ConfidenceResult(
        score=round(total * 10.0, 1),
        version=CONFIDENCE_VERSION,
        components=components,
        missing_components=missing,
    )
