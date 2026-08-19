"""Generación de candidates: donde se une todo.

    consenso sharp (fair)  +  predicción del modelo  +  mejor precio ejecutable
                                    |
                                    v
                       edge -> EV -> gates -> stake -> recomendación

Las dos separaciones que este módulo existe para respetar:

- **Precio de referencia vs precio ejecutable.** El edge se mide contra el
  consenso sharp; el EV se calcula con el mejor precio donde realmente puedes
  apostar. Usar el mismo precio para las dos cosas convierte el sistema en un
  detector de su propio ruido.
- **Candidate vs recomendación.** Se produce un candidate para cada selección
  evaluable, pase o no los gates.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ..core.edge import EdgeBreakdown, evaluate
from ..core.kelly import Stake, StakeConfig, recommend_stake
from ..filters.confidence import ConfidenceResult, compute_confidence
from ..filters.gates import FilterResult, GateInput, evaluate_gates
from ..models.base import ModelPrediction
from ..odds.consensus import ConsensusResult, PricePoint, best_available, line_age_seconds


@dataclass(frozen=True, slots=True)
class CandidateEvaluation:
    """Un candidate completamente evaluado, listo para persistir."""

    selection_id: int
    as_of: datetime
    breakdown: EdgeBreakdown
    prediction: ModelPrediction
    best_price: PricePoint
    reference_book_count: int
    dispersion: float
    line_age_seconds: float
    data_quality: float
    filters: FilterResult
    confidence: ConfidenceResult
    stake: Stake

    @property
    def is_recommended(self) -> bool:
        return self.filters.is_recommended

    @property
    def edge(self) -> float:
        """Edge de modelo: ¿sabemos algo que el mercado no?"""
        return self.breakdown.edge

    @property
    def total_edge(self) -> float:
        """Ventaja sobre el precio ejecutable. Es la que filtran los gates."""
        return self.breakdown.total_edge

    @property
    def expected_value(self) -> float:
        return self.breakdown.expected_value


def evaluate_selection(
    *,
    selection_id: int,
    consensus: ConsensusResult,
    prediction: ModelPrediction,
    points: list[PricePoint],
    executable_book_ids: set[int],
    as_of: datetime,
    data_quality: float = 1.0,
    sample_size: int | None = None,
    historical_calibration: float | None = None,
    stake_config: StakeConfig | None = None,
) -> CandidateEvaluation | None:
    """Evalúa una selección. `None` si falta el precio de referencia o el ejecutable.

    Devolver `None` en vez de un candidate degradado es deliberado: un candidate
    sin precio ejecutable no es una apuesta peor, es una apuesta que no existe, y
    colarla en las métricas contaminaría el denominador de todo.
    """
    fair_prob = consensus.fair_probabilities.get(selection_id)
    if fair_prob is None:
        return None

    best = best_available(points, selection_id, executable_book_ids, as_of=as_of)
    if best is None:
        return None

    # El precio de referencia es el consenso, no un book concreto: se expresa
    # como la cuota que corresponde a la probabilidad justa.
    reference_decimal = 1.0 / fair_prob

    breakdown = evaluate(
        model_prob=prediction.probability,
        market_fair_prob=fair_prob,
        reference_decimal=reference_decimal,
        best_decimal=best.price_decimal,
    )

    age = line_age_seconds(best, as_of)
    dispersion = consensus.dispersion(selection_id)

    filters = evaluate_gates(
        GateInput(
            total_edge=breakdown.total_edge,
            expected_value=breakdown.expected_value,
            line_age_seconds=age,
            reference_book_count=consensus.book_count,
            data_quality=data_quality,
            dispersion=dispersion,
            has_executable_price=True,
        )
    )

    confidence = compute_confidence(
        # La ventaja total es la operativa: determina el EV y es la que se
        # compara contra la incertidumbre del modelo.
        edge=breakdown.total_edge,
        uncertainty=prediction.uncertainty,
        dispersion=dispersion,
        data_quality=data_quality,
        sample_size=sample_size,
        line_age_seconds=age,
        historical_calibration=historical_calibration,
    )

    # El stake solo tiene sentido para lo que se recomienda. Calcularlo para un
    # candidate rechazado invitaría a leerlo como una sugerencia.
    stake = (
        recommend_stake(prediction.probability, best.price_decimal, stake_config)
        if filters.is_recommended
        else Stake(0.0, (stake_config or StakeConfig()).method, 0.0, 0.0, was_capped=False)
    )

    return CandidateEvaluation(
        selection_id=selection_id,
        as_of=as_of,
        breakdown=breakdown,
        prediction=prediction,
        best_price=best,
        reference_book_count=consensus.book_count,
        dispersion=dispersion,
        line_age_seconds=age,
        data_quality=data_quality,
        filters=filters,
        confidence=confidence,
        stake=stake,
    )


def evaluate_market(
    *,
    selections: tuple[int, ...],
    consensus: ConsensusResult,
    predictions: dict[int, ModelPrediction],
    points: list[PricePoint],
    executable_book_ids: set[int],
    as_of: datetime,
    **kwargs: object,
) -> list[CandidateEvaluation]:
    """Evalúa todas las selecciones de un mercado, ordenadas por edge descendente."""
    results = []
    for selection_id in selections:
        prediction = predictions.get(selection_id)
        if prediction is None:
            continue
        evaluation = evaluate_selection(
            selection_id=selection_id,
            consensus=consensus,
            prediction=prediction,
            points=points,
            executable_book_ids=executable_book_ids,
            as_of=as_of,
            **kwargs,  # type: ignore[arg-type]
        )
        if evaluation is not None:
            results.append(evaluation)
    return sorted(results, key=lambda e: e.total_edge, reverse=True)
