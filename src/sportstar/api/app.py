"""Aplicación FastAPI.

Contrato único del sistema. Diseñada para que un cliente nativo futuro no
requiera cambios de backend: JSON puro, sin estado de sesión, paginada, con
timestamps ISO-8601 en UTC.

Solo lectura. Las recomendaciones las produce el pipeline, no una petición HTTP:
mantener la API de lectura impide que un cliente altere el histórico de
decisiones, que debe ser inmutable para poder auditarlo.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.betting import Candidate, Recommendation, RecommendationReason
from ..db.enums import RecommendationStatus, Severity
from ..db.events import Event
from ..db.markets import Selection
from ..db.modeling import ModelVersion
from ..health import run_checks
from ..health.runner import HealthReport
from .deps import DEFAULT_LIMIT, MAX_LIMIT, get_session
from .performance import WINDOWS, compute_performance
from .schemas import (
    CandidateListOut,
    CandidateOut,
    DataHealthOut,
    HealthFindingOut,
    ModelOut,
    PerformanceOut,
    ReasonOut,
    RecommendationListOut,
    RecommendationOut,
)
from .serializers import Catalog, serialize_candidate, serialize_recommendation

app = FastAPI(
    title="Sportstar",
    version="0.1.0",
    description=(
        "Sports Betting Intelligence API. Solo lectura: las recomendaciones las "
        "produce el pipeline, no una petición HTTP."
    ),
)

SessionDep = Annotated[Session, Depends(get_session)]


def _load_context(
    session: Session, candidates: list[Candidate]
) -> tuple[Catalog, dict[int, Event], dict[int, Selection]]:
    """Trae de una vez los eventos y selecciones de un lote.

    Serializar treinta recomendaciones fila a fila haría cientos de consultas.
    """
    catalog = Catalog(session)
    event_ids = {c.event_id for c in candidates}
    selection_ids = {c.selection_id for c in candidates}
    events = {e.id: e for e in session.scalars(select(Event).where(Event.id.in_(event_ids)))}
    selections = {
        s.id: s for s in session.scalars(select(Selection).where(Selection.id.in_(selection_ids)))
    }
    return catalog, events, selections


@app.get("/v1/recommendations", response_model=RecommendationListOut, tags=["betting"])
def list_recommendations(
    session: SessionDep,
    sport: str | None = None,
    event_date: date | None = None,
    min_confidence: float = Query(0.0, ge=0, le=10),
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    offset: int = Query(0, ge=0),
) -> RecommendationListOut:
    """Apuestas recomendadas, **ordenadas por confianza descendente**.

    Es la pantalla principal: abrir el iPhone y saber en menos de diez segundos
    qué vale la pena hoy. Por eso el orden por defecto es la calidad, no la hora.
    """
    query = (
        select(Recommendation, Candidate)
        .join(Candidate, Candidate.id == Recommendation.candidate_id)
        .join(Event, Event.id == Candidate.event_id)
        .where(
            Recommendation.status == RecommendationStatus.ACTIVE,
            Recommendation.confidence_score >= min_confidence,
        )
    )
    if event_date is not None:
        query = query.where(Event.event_date == event_date)

    rows: list[tuple[Recommendation, Candidate]] = [
        (recommendation, candidate)
        for recommendation, candidate in session.execute(
            query.order_by(Recommendation.confidence_score.desc())
        )
    ]
    if sport:
        catalog = Catalog(session)
        keep: list[tuple[Recommendation, Candidate]] = []
        for recommendation, candidate in rows:
            event = session.get(Event, candidate.event_id)
            league = catalog.leagues.get(event.league_id) if event else None
            sport_row = catalog.sports.get(league.sport_id) if league else None
            if sport_row and sport_row.key == sport:
                keep.append((recommendation, candidate))
        rows = keep

    total = len(rows)
    page = rows[offset : offset + limit]
    if not page:
        return RecommendationListOut(items=[], total=total, limit=limit, offset=offset)

    catalog, events, selections = _load_context(session, [c for _, c in page])
    reasons_by_recommendation = _load_reasons(session, [r.id for r, _ in page])

    items = [
        serialize_recommendation(
            recommendation,
            serialize_candidate(
                candidate, events[candidate.event_id], selections[candidate.selection_id], catalog
            ),
            reasons_by_recommendation.get(recommendation.id, []),
        )
        for recommendation, candidate in page
    ]
    return RecommendationListOut(items=items, total=total, limit=limit, offset=offset)


def _load_reasons(session: Session, recommendation_ids: list[int]) -> dict[int, list[ReasonOut]]:
    rows = session.scalars(
        select(RecommendationReason)
        .where(RecommendationReason.recommendation_id.in_(recommendation_ids))
        .order_by(RecommendationReason.rank)
    )
    grouped: dict[int, list[ReasonOut]] = {}
    for row in rows:
        grouped.setdefault(row.recommendation_id, []).append(
            ReasonOut(
                rank=row.rank,
                factor_key=row.factor_key,
                factor_label=row.factor_label,
                contribution=row.contribution,
                source=str(row.source),
            )
        )
    return grouped


@app.get(
    "/v1/recommendations/{recommendation_id}", response_model=RecommendationOut, tags=["betting"]
)
def get_recommendation(recommendation_id: int, session: SessionDep) -> RecommendationOut:
    """Detalle con sus razones, derivadas de contribuciones reales del modelo."""
    recommendation = session.get(Recommendation, recommendation_id)
    if recommendation is None:
        raise HTTPException(404, f"recomendación {recommendation_id} no encontrada")

    candidate = session.get(Candidate, recommendation.candidate_id)
    if candidate is None:
        raise HTTPException(500, "recomendación sin candidate: linaje roto")

    catalog, events, selections = _load_context(session, [candidate])
    return serialize_recommendation(
        recommendation,
        serialize_candidate(
            candidate, events[candidate.event_id], selections[candidate.selection_id], catalog
        ),
        _load_reasons(session, [recommendation.id]).get(recommendation.id, []),
    )


@app.get("/v1/candidates", response_model=CandidateListOut, tags=["betting"])
def list_candidates(
    session: SessionDep,
    event_date: date | None = None,
    min_edge: float | None = None,
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    offset: int = Query(0, ge=0),
) -> CandidateListOut:
    """**Todos** los candidates, pasaran o no los filtros.

    Existe para poder auditar el filtro por separado del modelo: ver qué se
    descartó y con qué números es lo que permite decidir si un umbral está
    aportando o solo estorbando.
    """
    query = select(Candidate).join(Event, Event.id == Candidate.event_id)
    if event_date is not None:
        query = query.where(Event.event_date == event_date)
    if min_edge is not None:
        query = query.where(Candidate.edge >= min_edge)

    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = list(session.scalars(query.order_by(Candidate.as_of.desc()).limit(limit).offset(offset)))
    if not rows:
        return CandidateListOut(items=[], total=total, limit=limit, offset=offset)

    catalog, events, selections = _load_context(session, rows)
    recommended = set(
        session.scalars(
            select(Recommendation.candidate_id).where(
                Recommendation.candidate_id.in_([c.id for c in rows])
            )
        )
    )
    items: list[CandidateOut] = []
    for candidate in rows:
        out = serialize_candidate(
            candidate, events[candidate.event_id], selections[candidate.selection_id], catalog
        )
        items.append(out.model_copy(update={"is_recommended": candidate.id in recommended}))
    return CandidateListOut(items=items, total=total, limit=limit, offset=offset)


@app.get("/v1/performance", response_model=PerformanceOut, tags=["analytics"])
def get_performance(
    session: SessionDep,
    window: str = Query("30d", pattern="^(7d|30d|90d|all)$"),
) -> PerformanceOut:
    """Rendimiento de una ventana.

    Devuelve siempre el tamaño de muestra y si es interpretable. Un ROI sin `n`
    invita a leer ruido como señal, y ese es el error central que el sistema
    intenta evitar.
    """
    if window not in WINDOWS:
        raise HTTPException(422, f"ventana inválida: {window}")
    return compute_performance(session, window=window)


@app.get("/v1/models", response_model=list[ModelOut], tags=["analytics"])
def list_models(session: SessionDep) -> list[ModelOut]:
    """Modelos registrados. Cada predicción histórica apunta a uno de estos."""
    return [
        ModelOut(
            id=row.id,
            name=row.name,
            version=row.version,
            market_type=row.market_type,
            algorithm=row.algorithm,
            is_active=row.is_active,
            trained_at=row.trained_at,
            metrics=row.metrics,
        )
        for row in session.scalars(select(ModelVersion).order_by(ModelVersion.name))
    ]


@app.get("/v1/health/data", response_model=DataHealthOut, tags=["ops"])
def get_data_health(session: SessionDep) -> DataHealthOut:
    """Estado de los pipelines de datos.

    `is_healthy` es False solo con CRITICAL abiertos. Un WARNING permanente que
    marcase el sistema como enfermo entrenaría a cualquiera a ignorar el
    indicador.
    """
    report: HealthReport = run_checks(session)

    def render(severity: Severity) -> list[HealthFindingOut]:
        return [
            HealthFindingOut(
                check_name=f.check_name,
                severity=str(f.severity),
                message=f.message,
                entity_type=f.entity_type,
                entity_id=f.entity_id,
                detected_at=report.checked_at,
            )
            for f in report.by_severity(severity)
        ]

    return DataHealthOut(
        is_healthy=report.is_healthy,
        checked_at=report.checked_at,
        critical=render(Severity.CRITICAL),
        warning=render(Severity.WARNING),
        info=render(Severity.INFO),
    )


@app.get("/v1/health", tags=["ops"])
def liveness() -> dict[str, str]:
    """Liveness. No toca la base: responde si el proceso está vivo, nada más."""
    return {"status": "ok", "time": datetime.now(UTC).isoformat()}
