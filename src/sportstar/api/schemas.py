"""Esquemas de respuesta de la API.

La API es el **contrato único** del sistema. Streamlit, si algún día se usa, es
herramienta interna de debug; el producto se consume por aquí. Acoplar la UI al
backend cerraría la puerta al iPhone, que es el objetivo declarado.

Reglas que se aplican en todo el módulo:

- Timestamps ISO-8601 en UTC, siempre con zona explícita.
- Nada de estado de sesión: cualquier cliente futuro (PWA, React Native, SwiftUI)
  debe poder consumirla sin cambios de backend.
- Las probabilidades viajan como fracciones (0.587), no como porcentajes. El
  formateo es decisión de presentación; mezclar ambas unidades en el transporte
  es cómo aparecen los errores de factor 100.
- **Ninguna métrica viaja sin su tamaño de muestra.** Un ROI sin `n` invita a
  leer ruido como señal, y ese es el error central que este proyecto intenta
  evitar.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ReasonOut(BaseModel):
    """Contribución real de un factor. Nunca un texto inventado."""

    rank: int
    factor_key: str
    factor_label: str
    contribution: float = Field(description="En puntos de probabilidad (0.021 = +2.1%)")
    source: str


class PriceOut(BaseModel):
    sportsbook: str
    american: float
    decimal: float
    captured_at: datetime


class EventOut(BaseModel):
    id: int
    sport: str
    league: str
    home_team: str
    away_team: str
    start_time: datetime
    status: str
    game_number: int = 1


class CandidateOut(BaseModel):
    """Un par (predicción, precio) evaluado, se recomiende o no.

    Las tres probabilidades viajan en campos separados. Confundir la implícita
    (con vig) con la justa (sin vig) infla el edge de forma sistemática, y
    fusionarlas aquí reintroduciría por la API el error que el esquema evita.
    """

    id: int
    event: EventOut
    selection_label: str
    market: str

    model_probability: float
    market_implied_probability: float = Field(description="CON vig. Es el break-even.")
    market_fair_probability: float = Field(description="SIN vig, consenso sharp.")

    edge: float = Field(description="model - fair: ¿sabemos algo que el mercado no?")
    structural_edge: float = Field(description="fair - implícita del mejor precio.")
    total_edge: float = Field(description="Ventaja total. Determina el signo del EV.")
    expected_roi: float

    best_price: PriceOut
    reference_book_count: int
    novig_method: str
    line_age_seconds: int | None
    is_recommended: bool
    as_of: datetime


class RecommendationOut(BaseModel):
    """Un candidate que superó todos los gates."""

    id: int
    candidate: CandidateOut
    confidence_score: float = Field(ge=0, le=10)
    confidence_version: int = Field(
        description="0 = pesos provisionales, sin calibrar contra histórico."
    )
    recommended_stake_units: float
    sizing_method: str
    was_stake_capped: bool
    filter_version: str
    correlation_group: str | None
    reasons: list[ReasonOut] = []
    created_at: datetime


class CandidateListOut(BaseModel):
    items: list[CandidateOut]
    total: int
    limit: int
    offset: int


class RecommendationListOut(BaseModel):
    items: list[RecommendationOut]
    total: int
    limit: int
    offset: int


class PerformanceOut(BaseModel):
    """Rendimiento de un periodo.

    `n_bets` y `n_candidates` son obligatorios y `metrics_are_interpretable`
    marca explícitamente si la muestra da para leer el ROI. Demostrar un ROI real
    del +3% exige del orden de 5.000-8.000 apuestas; un beat-close del 55%, unas
    500-1.000. Con tres cifras de muestra, el ROI es varianza con formato de
    porcentaje.
    """

    window: str
    group_by: str | None
    n_bets: int
    n_candidates: int
    wins: int
    losses: int
    pushes: int
    units_staked: float
    units_won: float
    roi: float | None
    win_rate: float | None
    beat_close_rate: float | None
    model_beat_close_rate: float | None = Field(
        description="Sobre TODOS los candidates, no solo apuestas. Muestra mucho mayor."
    )
    metrics_are_interpretable: bool
    interpretation_note: str


class ModelOut(BaseModel):
    id: int
    name: str
    version: str
    market_type: str
    algorithm: str
    is_active: bool
    trained_at: datetime | None
    metrics: dict[str, float] | None


class HealthFindingOut(BaseModel):
    check_name: str
    severity: str
    message: str
    entity_type: str | None
    entity_id: int | None
    detected_at: datetime


class DataHealthOut(BaseModel):
    is_healthy: bool = Field(description="False solo si hay algún CRITICAL abierto.")
    checked_at: datetime
    critical: list[HealthFindingOut]
    warning: list[HealthFindingOut]
    info: list[HealthFindingOut]
