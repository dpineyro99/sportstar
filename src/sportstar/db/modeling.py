"""Features, versiones de modelo y predicciones.

El invariante central del sistema vive aquí: `as_of` no es metadata, es parte de
la clave. El mismo evento tiene features distintas a las 10:00 y a las 18:00, y
ambas son correctas para su momento.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    Float,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from .base import Base, JsonDict, JsonList, TimestampMixin, UtcDateTime


class FeatureSet(Base, TimestampMixin):
    """Definición versionada de un conjunto de features."""

    __tablename__ = "feature_sets"
    __table_args__ = (UniqueConstraint("name", "version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    sport_id: Mapped[int] = mapped_column(ForeignKey("sports.id"), index=True)
    name: Mapped[str] = mapped_column(String(96))
    version: Mapped[str] = mapped_column(String(32))
    spec: Mapped[JsonDict] = mapped_column(JSON)


class EventFeature(Base):
    """Features de un equipo en un evento, calculadas **a fecha `as_of`**.

    Invariante verificable: ninguna feature en una fila con `as_of = T` puede
    derivarse de un registro con `observed_at >= T`. `validation/sanity.py` lo
    comprueba y falla duro si se viola.
    """

    __tablename__ = "event_features"
    __table_args__ = (
        UniqueConstraint("event_id", "team_id", "feature_set_id", "as_of"),
        Index("ix_event_features_event_as_of", "event_id", "as_of"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"))
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    feature_set_id: Mapped[int] = mapped_column(ForeignKey("feature_sets.id"))
    as_of: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    features: Mapped[JsonDict] = mapped_column(JSON)
    data_quality_score: Mapped[float] = mapped_column(Float, default=1.0)
    missing_features: Mapped[JsonList | None] = mapped_column(JSON)
    computed_at: Mapped[datetime] = mapped_column(UtcDateTime)


class ModelVersion(Base, TimestampMixin):
    """Registro de modelos.

    Los splits son **temporales**, nunca aleatorios: barajar partidos mezcla
    futuro con pasado y produce métricas que no se reproducen en producción.

    `market_consensus_v1` (el consenso sharp sin vig) se registra aquí como
    cualquier otro modelo. Es la vara: ningún modelo pasa a `is_active` si no
    bate su Brier score.
    """

    __tablename__ = "model_versions"
    __table_args__ = (UniqueConstraint("name", "version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(96))
    version: Mapped[str] = mapped_column(String(32))
    sport_id: Mapped[int] = mapped_column(ForeignKey("sports.id"), index=True)
    market_type: Mapped[str] = mapped_column(String(24))
    algorithm: Mapped[str] = mapped_column(String(64))
    hyperparams: Mapped[JsonDict | None] = mapped_column(JSON)
    feature_set_id: Mapped[int | None] = mapped_column(ForeignKey("feature_sets.id"))
    train_start: Mapped[date | None] = mapped_column(Date)
    train_end: Mapped[date | None] = mapped_column(Date)
    val_start: Mapped[date | None] = mapped_column(Date)
    val_end: Mapped[date | None] = mapped_column(Date)
    test_start: Mapped[date | None] = mapped_column(Date)
    test_end: Mapped[date | None] = mapped_column(Date)
    metrics: Mapped[JsonDict | None] = mapped_column(JSON)
    artifact_path: Mapped[str | None] = mapped_column(String(512))
    artifact_hash: Mapped[str | None] = mapped_column(String(96))
    trained_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, index=True)


class Prediction(Base, TimestampMixin):
    """Probabilidad producida por un modelo para una selección en un instante.

    `event_features_id` + `model_version_id` hacen cualquier predicción histórica
    reconstruible bit a bit.

    `prob_lower/upper` existen porque el Confidence Score necesita el edge medido
    en desviaciones estándar, no en puntos porcentuales: un edge de 3 puntos con
    un modelo muy incierto no es la misma apuesta que 3 puntos con un modelo
    estrecho.
    """

    __tablename__ = "predictions"
    __table_args__ = (
        UniqueConstraint("selection_id", "model_version_id", "as_of"),
        Index("ix_predictions_selection_as_of", "selection_id", "as_of"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), index=True)
    selection_id: Mapped[int] = mapped_column(ForeignKey("selections.id"))
    model_version_id: Mapped[int] = mapped_column(ForeignKey("model_versions.id"), index=True)
    event_features_id: Mapped[int | None] = mapped_column(ForeignKey("event_features.id"))
    probability: Mapped[float] = mapped_column(Float)
    prob_lower: Mapped[float | None] = mapped_column(Float)
    prob_upper: Mapped[float | None] = mapped_column(Float)
    as_of: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
