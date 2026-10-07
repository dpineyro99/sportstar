"""Observabilidad, calidad de datos y backtesting."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from .base import Base, JsonDict, JsonList, TimestampMixin, UtcDateTime
from .enums import BetOutcome, EntityType, JobStatus, Severity


class JobRun(Base):
    """Ejecución de un job programado.

    Un job que no encuentra nada es un fallo, no un éxito silencioso: si
    `matched == 0` con `received > 0`, el estado es FAILED con motivo. Los
    procesos que fallan en silencio son la causa raíz de casi todo backtest
    engañoso.
    """

    __tablename__ = "job_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_name: Mapped[str] = mapped_column(String(96), index=True)
    sport_key: Mapped[str | None] = mapped_column(String(32), index=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    started_at: Mapped[datetime] = mapped_column(UtcDateTime, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, native_enum=False, length=12), index=True
    )
    counters: Mapped[JsonDict | None] = mapped_column(JSON)
    error_summary: Mapped[str | None] = mapped_column(String(2048))


class DataHealthCheck(Base):
    """Problema detectado por un check automático."""

    __tablename__ = "data_health_checks"
    __table_args__ = (Index("ix_data_health_checks_open", "check_name", "resolved_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    check_name: Mapped[str] = mapped_column(String(96), index=True)
    severity: Mapped[Severity] = mapped_column(
        Enum(Severity, native_enum=False, length=12), index=True
    )
    entity_type: Mapped[str | None] = mapped_column(String(24))
    entity_id: Mapped[int | None] = mapped_column(Integer)
    message: Mapped[str] = mapped_column(String(1024))
    detected_at: Mapped[datetime] = mapped_column(UtcDateTime, index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(UtcDateTime)


class UnmatchedEntity(Base):
    """Cola de revisión del entity resolution.

    Lo que no empareja no se descarta en silencio: se escribe aquí y se cuenta en
    el log del job.
    """

    __tablename__ = "unmatched_entities"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(64), index=True)
    entity_type: Mapped[EntityType] = mapped_column(Enum(EntityType, native_enum=False, length=16))
    raw_value: Mapped[str] = mapped_column(String(256), index=True)
    context: Mapped[JsonDict | None] = mapped_column(JSON)
    first_seen_at: Mapped[datetime] = mapped_column(UtcDateTime)
    last_seen_at: Mapped[datetime] = mapped_column(UtcDateTime)
    occurrences: Mapped[int] = mapped_column(Integer, default=1)
    resolved_to_id: Mapped[int | None] = mapped_column(Integer)


class Backtest(Base, TimestampMixin):
    """Ejecución de backtest.

    `passed_sanity = False` **bloquea** la presentación de métricas. Un backtest
    que dispara un check no produce un número con asterisco: produce un error que
    hay que investigar antes de mirar el ROI.
    """

    __tablename__ = "backtests"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    model_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("model_versions.id"), index=True
    )
    feature_set_id: Mapped[int | None] = mapped_column(ForeignKey("feature_sets.id"))
    filter_version: Mapped[str | None] = mapped_column(String(32))
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    config: Mapped[JsonDict | None] = mapped_column(JSON)
    metrics: Mapped[JsonDict | None] = mapped_column(JSON)
    sanity_checks: Mapped[JsonList | None] = mapped_column(JSON)
    passed_sanity: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    # El test set temporal se toca una vez; cada iteración lo convierte en train.
    test_set_uses: Mapped[int] = mapped_column(Integer, default=0)


class BacktestBet(Base):
    """Apuesta simulada dentro de un backtest."""

    __tablename__ = "backtest_bets"
    __table_args__ = (Index("ix_backtest_bets_backtest_as_of", "backtest_id", "as_of"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    backtest_id: Mapped[int] = mapped_column(ForeignKey("backtests.id"), index=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"))
    selection_id: Mapped[int] = mapped_column(ForeignKey("selections.id"))
    as_of: Mapped[datetime] = mapped_column(UtcDateTime)
    model_prob: Mapped[float] = mapped_column(Float)
    fair_prob: Mapped[float] = mapped_column(Float)
    edge: Mapped[float] = mapped_column(Float)
    price_decimal: Mapped[float] = mapped_column(Float)
    stake_units: Mapped[float] = mapped_column(Float)
    outcome: Mapped[BetOutcome | None] = mapped_column(
        Enum(BetOutcome, native_enum=False, length=8)
    )
    profit_units: Mapped[float | None] = mapped_column(Float)
    clv_price: Mapped[float | None] = mapped_column(Float)
