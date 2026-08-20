"""Candidates, recomendaciones, apuestas y resultados."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from .base import Base, JsonList, TimestampMixin, UtcDateTime
from .enums import BetOutcome, ReasonSource, RecommendationStatus


class Candidate(Base, TimestampMixin):
    """Par (predicción, precio) evaluado. Se persiste **aunque no se recomiende**.

    Guardar los rechazados es lo que permite responder después "¿qué habría
    pasado con umbral 2% en vez de 3%?" sin re-simular nada.

    Las tres probabilidades viven en columnas separadas y nunca se sobrescriben
    entre sí. Confundir `market_implied_prob` (con vig) con `market_fair_prob`
    (sin vig) infla el edge sistemáticamente; aquí es estructuralmente imposible.
    """

    __tablename__ = "candidates"
    __table_args__ = (
        Index("ix_candidates_event_as_of", "event_id", "as_of"),
        Index("ix_candidates_as_of", "as_of"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), index=True)
    selection_id: Mapped[int] = mapped_column(ForeignKey("selections.id"), index=True)
    prediction_id: Mapped[int] = mapped_column(ForeignKey("predictions.id"), index=True)

    # Precio de referencia: define la probabilidad justa (consenso sharp).
    #
    # Es una LISTA de ids, no una FK única. El consenso se calcula sobre los
    # snapshots de N books de referencia, así que con una sola referencia sería
    # imposible reconstruirlo — y reconstruir cualquier apuesta histórica es
    # requisito del sistema, no una comodidad. Se guardan todos los snapshots que
    # entraron en el promedio.
    reference_odds_snapshot_ids: Mapped[JsonList] = mapped_column(JSON)
    reference_book_count: Mapped[int] = mapped_column(Integer)
    market_implied_prob: Mapped[float] = mapped_column(Float)  # CON vig
    market_fair_prob: Mapped[float] = mapped_column(Float)  # SIN vig
    novig_method: Mapped[str] = mapped_column(String(16))
    # Discrepancia entre books de referencia: cuando los sharp no se ponen de
    # acuerdo, el mercado está menos seguro y la confianza debe bajar.
    reference_dispersion: Mapped[float | None] = mapped_column(Float)

    # Precio ejecutable: define el EV.
    best_odds_snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("odds_snapshots.id"))
    best_sportsbook_id: Mapped[int | None] = mapped_column(ForeignKey("sportsbooks.id"))
    best_price_american: Mapped[float | None] = mapped_column(Float)
    best_price_decimal: Mapped[float | None] = mapped_column(Float)

    model_prob: Mapped[float] = mapped_column(Float)
    edge: Mapped[float] = mapped_column(Float, index=True)
    structural_edge: Mapped[float | None] = mapped_column(Float)
    expected_value: Mapped[float] = mapped_column(Float)
    expected_roi: Mapped[float] = mapped_column(Float)

    line_age_seconds: Mapped[int | None] = mapped_column(Integer)
    data_quality_score: Mapped[float] = mapped_column(Float, default=1.0)
    model_agreement: Mapped[float | None] = mapped_column(Float)

    as_of: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)

    # Rellenado por el job de cierre para TODO candidate (ARCHITECTURE.md §4.6).
    # Es la base de la validación sin apuestas: `model_beat_close` responde si
    # nuestra probabilidad estaba más cerca del cierre que la del mercado en su
    # momento, sobre el slate entero en vez de solo sobre lo apostado.
    closing_odds_snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("odds_snapshots.id"))
    closing_fair_prob: Mapped[float | None] = mapped_column(Float)
    clv_probability: Mapped[float | None] = mapped_column(Float)
    model_beat_close: Mapped[bool | None] = mapped_column(Boolean)


class Recommendation(Base, TimestampMixin):
    """Candidate que superó todos los gates del filtro."""

    __tablename__ = "recommendations"

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.id"), index=True)
    confidence_score: Mapped[float] = mapped_column(Float, index=True)
    confidence_version: Mapped[int] = mapped_column(Integer, default=0)
    recommended_stake_units: Mapped[float] = mapped_column(Float)
    sizing_method: Mapped[str] = mapped_column(String(24))
    kelly_fraction: Mapped[float | None] = mapped_column(Float)
    was_stake_capped: Mapped[bool] = mapped_column(Boolean, default=False)
    filter_version: Mapped[str] = mapped_column(String(32))
    passed_filters: Mapped[JsonList | None] = mapped_column(JSON)
    failed_filters: Mapped[JsonList | None] = mapped_column(JSON)
    # Se guarda desde el día 1 aunque el portfolio engine llegue en Phase 9:
    # añadir el campo después es barato, reconstruir la exposición histórica no.
    correlation_group: Mapped[str | None] = mapped_column(String(96), index=True)
    status: Mapped[RecommendationStatus] = mapped_column(
        Enum(RecommendationStatus, native_enum=False, length=16),
        default=RecommendationStatus.ACTIVE,
        index=True,
    )


class RecommendationReason(Base, TimestampMixin):
    """Contribución real de un factor a la recomendación.

    Sale de coeficientes del modelo o de SHAP, nunca de texto libre. Ninguna fila
    puede existir sin un factor que el modelo haya consumido de verdad: si el
    modelo no usa lesiones, la explicación no menciona lesiones aunque quede peor.
    """

    __tablename__ = "recommendation_reasons"

    id: Mapped[int] = mapped_column(primary_key=True)
    recommendation_id: Mapped[int] = mapped_column(ForeignKey("recommendations.id"), index=True)
    rank: Mapped[int] = mapped_column(Integer)
    factor_key: Mapped[str] = mapped_column(String(96))
    factor_label: Mapped[str] = mapped_column(String(160))
    contribution: Mapped[float] = mapped_column(Float)  # en puntos de probabilidad
    source: Mapped[ReasonSource] = mapped_column(Enum(ReasonSource, native_enum=False, length=24))


class Bet(Base, TimestampMixin):
    """Apuesta registrada. En Phase 4 todas son `is_paper = True`.

    Precio, línea y probabilidades se **copian** en vez de referenciarse:
    redundancia deliberada, porque una apuesta es un hecho histórico congelado y
    no debe cambiar si se recalcula algo aguas arriba.
    """

    __tablename__ = "bets"

    id: Mapped[int] = mapped_column(primary_key=True)
    recommendation_id: Mapped[int | None] = mapped_column(
        ForeignKey("recommendations.id"), index=True
    )
    selection_id: Mapped[int] = mapped_column(ForeignKey("selections.id"), index=True)
    sportsbook_id: Mapped[int] = mapped_column(ForeignKey("sportsbooks.id"), index=True)
    is_paper: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    stake_units: Mapped[float] = mapped_column(Float)
    price_american_taken: Mapped[float] = mapped_column(Float)
    price_decimal_taken: Mapped[float] = mapped_column(Float)
    line_taken: Mapped[float | None] = mapped_column(Float)
    fair_prob_at_bet: Mapped[float] = mapped_column(Float)
    model_prob_at_bet: Mapped[float] = mapped_column(Float)
    placed_at: Mapped[datetime] = mapped_column(UtcDateTime, index=True)
    model_version_id: Mapped[int | None] = mapped_column(ForeignKey("model_versions.id"))
    filter_version: Mapped[str | None] = mapped_column(String(32))
    confidence_version: Mapped[int | None] = mapped_column(Integer)


class BetResult(Base, TimestampMixin):
    """Liquidación y CLV.

    El CLV se calcula aquí porque el closing line solo existe tras el cierre. Si
    el job de captura falló, estos campos quedan NULL — y eso es un incidente de
    Data Health, no un dato ausente cualquiera: es irrecuperable.
    """

    __tablename__ = "bet_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    bet_id: Mapped[int] = mapped_column(ForeignKey("bets.id"), unique=True)
    outcome: Mapped[BetOutcome] = mapped_column(Enum(BetOutcome, native_enum=False, length=8))
    profit_units: Mapped[float] = mapped_column(Float)
    closing_odds_snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("odds_snapshots.id"))
    closing_price_decimal: Mapped[float | None] = mapped_column(Float)
    closing_fair_prob: Mapped[float | None] = mapped_column(Float)
    clv_price: Mapped[float | None] = mapped_column(Float)
    clv_probability: Mapped[float | None] = mapped_column(Float)
    beat_closing_line: Mapped[bool | None] = mapped_column(Boolean)
    settled_at: Mapped[datetime] = mapped_column(UtcDateTime, index=True)
    settlement_source: Mapped[str | None] = mapped_column(String(64))
