"""Persistencia del pipeline: evaluaciones en memoria -> filas de la base.

Se persiste **todo** candidate, pase o no los filtros. Es lo que permite
responder después "¿qué habría pasado con umbral 2% en vez de 3%?" sin volver a
simular nada, y lo que separa la evaluación del modelo (sobre todos los
candidates) de la evaluación del filtro (solo sobre recomendaciones).

Cada fila queda con linaje completo —predicción, versión de modelo, snapshots de
referencia, snapshot ejecutable— para poder reconstruir cualquier apuesta
histórica.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from ..core.odds import decimal_to_american
from ..db.betting import Candidate, Recommendation, RecommendationReason
from ..db.enums import RecommendationStatus
from ..db.modeling import ModelVersion, Prediction
from ..filters.gates import FILTER_VERSION
from .candidates import CandidateEvaluation
from .reasons import build_reasons


class PersistenceError(RuntimeError):
    """No se puede persistir una evaluación sin linaje completo."""


@dataclass
class PersistResult:
    """Qué se escribió. Los contadores alimentan el `JobReport`."""

    candidates: list[Candidate] = field(default_factory=list)
    recommendations: list[Recommendation] = field(default_factory=list)
    predictions: list[Prediction] = field(default_factory=list)

    @property
    def counters(self) -> dict[str, int]:
        return {
            "predictions": len(self.predictions),
            "candidates": len(self.candidates),
            "recommendations": len(self.recommendations),
        }


def persist_evaluation(
    session: Session,
    evaluation: CandidateEvaluation,
    *,
    event_id: int,
    model_version: ModelVersion,
    consensus_snapshot_ids: tuple[int, ...],
    best_book_name: str | None = None,
    data_quality: float | None = None,
) -> tuple[Prediction, Candidate, Recommendation | None]:
    """Escribe una evaluación completa con su linaje.

    Falla si el precio ejecutable no tiene `snapshot_id`. Es deliberado: un
    candidate cuyo precio no se puede señalar en `odds_snapshots` no es
    reconstruible, y un `NULL` ahí se descubriría meses después al intentar
    auditar una apuesta. Mejor romper ahora, en el sitio donde está la causa.
    """
    if evaluation.best_price.snapshot_id is None:
        raise PersistenceError(
            f"selección {evaluation.selection_id}: el mejor precio no tiene snapshot_id. "
            "Solo se persisten precios cargados desde la base, nunca sintéticos."
        )

    prediction = Prediction(
        event_id=event_id,
        selection_id=evaluation.selection_id,
        model_version_id=model_version.id,
        probability=evaluation.prediction.probability,
        prob_lower=evaluation.prediction.lower,
        prob_upper=evaluation.prediction.upper,
        as_of=evaluation.as_of,
    )
    session.add(prediction)
    session.flush()

    breakdown = evaluation.breakdown
    candidate = Candidate(
        event_id=event_id,
        selection_id=evaluation.selection_id,
        prediction_id=prediction.id,
        reference_odds_snapshot_ids=list(consensus_snapshot_ids),
        reference_book_count=evaluation.reference_book_count,
        market_implied_prob=breakdown.market_implied_prob,
        market_fair_prob=breakdown.market_fair_prob,
        novig_method=str(evaluation.novig_method),
        reference_dispersion=evaluation.dispersion,
        best_odds_snapshot_id=evaluation.best_price.snapshot_id,
        best_sportsbook_id=evaluation.best_price.sportsbook_id,
        best_price_decimal=breakdown.best_decimal,
        best_price_american=decimal_to_american(breakdown.best_decimal),
        model_prob=evaluation.prediction.probability,
        edge=breakdown.edge,
        structural_edge=breakdown.structural_edge,
        expected_value=breakdown.expected_value,
        expected_roi=breakdown.expected_value,
        line_age_seconds=int(evaluation.line_age_seconds),
        data_quality_score=data_quality if data_quality is not None else evaluation.data_quality,
        model_agreement=evaluation.dispersion,
        as_of=evaluation.as_of,
    )
    session.add(candidate)
    session.flush()

    if not evaluation.is_recommended:
        return prediction, candidate, None

    recommendation = Recommendation(
        candidate_id=candidate.id,
        confidence_score=evaluation.confidence.score,
        confidence_version=evaluation.confidence.version,
        recommended_stake_units=evaluation.stake.units,
        sizing_method=str(evaluation.stake.method),
        kelly_fraction=evaluation.stake.full_kelly_fraction,
        was_stake_capped=evaluation.stake.was_capped,
        filter_version=evaluation.filters.version or FILTER_VERSION,
        passed_filters=list(evaluation.filters.passed),
        failed_filters=list(evaluation.filters.failed),
        correlation_group=correlation_group(event_id, evaluation),
        status=RecommendationStatus.ACTIVE,
    )
    session.add(recommendation)
    session.flush()

    for rank, reason in enumerate(build_reasons(breakdown, best_book_name), start=1):
        session.add(
            RecommendationReason(
                recommendation_id=recommendation.id,
                rank=rank,
                factor_key=reason.factor_key,
                factor_label=reason.factor_label,
                contribution=reason.contribution,
                source=reason.source,
            )
        )
    session.flush()
    return prediction, candidate, recommendation


def correlation_group(event_id: int, evaluation: CandidateEvaluation) -> str:
    """Agrupa apuestas cuya suerte va junta.

    De momento, todas las selecciones del mismo evento. Es una aproximación
    burda —el ML del local y el over no están perfectamente correlacionados— pero
    es conservadora en la dirección correcta: agrupar de más limita exposición,
    agrupar de menos la multiplica sin que nadie se entere.

    El portfolio engine de Phase 9 refinará la definición. Guardarla desde ahora
    cuesta un campo; reconstruir la exposición histórica sin ella es imposible.
    """
    return f"event:{event_id}"


def persist_evaluations(
    session: Session,
    evaluations: list[CandidateEvaluation],
    *,
    event_id: int,
    model_version: ModelVersion,
    consensus_snapshot_ids: tuple[int, ...],
    book_names: dict[int, str] | None = None,
) -> PersistResult:
    """Persiste un lote completo. Los contadores alimentan el `JobReport`."""
    result = PersistResult()
    names = book_names or {}

    for evaluation in evaluations:
        prediction, candidate, recommendation = persist_evaluation(
            session,
            evaluation,
            event_id=event_id,
            model_version=model_version,
            consensus_snapshot_ids=consensus_snapshot_ids,
            best_book_name=names.get(evaluation.best_price.sportsbook_id),
        )
        result.predictions.append(prediction)
        result.candidates.append(candidate)
        if recommendation is not None:
            result.recommendations.append(recommendation)

    return result
