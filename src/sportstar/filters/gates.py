"""Gates que convierten un candidate en una recomendación.

La separación candidate/recommendation existe para poder medir el filtro por
separado del modelo. Se persisten **todos** los candidates, incluidos los
rechazados: es lo que permite responder después "¿qué habría pasado con umbral
2% en vez de 3%?" sin volver a simular nada.

Una apuesta puede tener edge matemático y aun así no merecer recomendación: un
edge de 6 puntos calculado sobre un precio de hace media hora, o sobre un único
book de referencia, no es una oportunidad, es un artefacto.

**Los umbrales de v1 son provisionales.** Se recalibran en Phase 3, que es
exactamente la pregunta "¿qué edge mínimo funciona?". Van versionados
(`FILTER_VERSION`) para que un cambio de umbral no invalide la lectura del
histórico.
"""

from __future__ import annotations

from dataclasses import dataclass

FILTER_VERSION = "v1"

# Se filtra por la VENTAJA TOTAL sobre el precio ejecutable
# (`core.edge.total_edge`), no por el edge de modelo. Filtrar por el edge de
# modelo dejaría a `market_consensus_v1` sin recomendar nada —su edge es 0 por
# construcción— y con él se iría toda la medición del edge estructural, que es
# el suelo del sistema.
MIN_EDGE = 0.02
MIN_EXPECTED_VALUE = 0.01  # tras costes
MAX_LINE_AGE_SECONDS = 600.0  # 10 minutos
MIN_REFERENCE_BOOKS = 2  # con uno solo no hay consenso, hay una opinión
MIN_DATA_QUALITY = 0.80
MAX_DISPERSION_RATIO = 0.50  # dispersión entre books frente al tamaño del edge


@dataclass(frozen=True, slots=True)
class GateInput:
    """Todo lo que los gates necesitan. Sin base de datos, para poder testearlos."""

    total_edge: float  # ventaja sobre el precio ejecutable: edge + structural_edge
    expected_value: float
    line_age_seconds: float | None
    reference_book_count: int
    data_quality: float
    dispersion: float | None
    has_executable_price: bool


@dataclass(frozen=True, slots=True)
class FilterResult:
    passed: tuple[str, ...]
    failed: tuple[str, ...]
    version: str = FILTER_VERSION

    @property
    def is_recommended(self) -> bool:
        return not self.failed


def evaluate_gates(gate_input: GateInput) -> FilterResult:
    """Aplica todos los gates. No corta al primer fallo.

    Evaluarlos todos cuesta lo mismo y deja registrado el conjunto completo de
    motivos, que es lo que permite después contar cuál rechaza más y decidir si
    ese umbral está aportando o simplemente estorbando.
    """
    checks: list[tuple[str, bool]] = [
        ("min_edge", gate_input.total_edge >= MIN_EDGE),
        ("min_expected_value", gate_input.expected_value >= MIN_EXPECTED_VALUE),
        (
            "line_freshness",
            gate_input.line_age_seconds is not None
            and gate_input.line_age_seconds <= MAX_LINE_AGE_SECONDS,
        ),
        ("reference_books", gate_input.reference_book_count >= MIN_REFERENCE_BOOKS),
        ("data_quality", gate_input.data_quality >= MIN_DATA_QUALITY),
        ("executable_price", gate_input.has_executable_price),
        (
            "model_agreement",
            gate_input.dispersion is None
            or abs(gate_input.total_edge) <= 0.0
            or gate_input.dispersion / abs(gate_input.total_edge) <= MAX_DISPERSION_RATIO,
        ),
    ]
    return FilterResult(
        passed=tuple(name for name, ok in checks if ok),
        failed=tuple(name for name, ok in checks if not ok),
    )
