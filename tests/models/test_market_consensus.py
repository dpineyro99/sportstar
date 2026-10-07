"""El baseline de mercado."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from sportstar.models import MarketConsensusModel, ModelPrediction, SportModel
from sportstar.odds import PricePoint, consensus_fair_probabilities

T0 = datetime(2026, 8, 19, 18, 0, tzinfo=UTC)
HOME, AWAY = 1, 2
SHARP_A, SHARP_B = 10, 11
SELECTIONS = (HOME, AWAY)


def consensus(*books: tuple[int, float, float]):
    points: list[PricePoint] = []
    for book, home, away in books:
        points += [PricePoint(HOME, book, home, T0), PricePoint(AWAY, book, away, T0)]
    result = consensus_fair_probabilities(points, SELECTIONS, {b for b, _, _ in books}, as_of=T0)
    assert result is not None
    return result


class TestContract:
    def test_satisfies_the_sport_model_protocol(self) -> None:
        # Añadir un modelo no debe requerir tocar nada aguas abajo.
        assert isinstance(MarketConsensusModel(), SportModel)

    def test_is_named_and_versioned(self) -> None:
        model = MarketConsensusModel()
        assert model.name == "market_consensus"
        assert model.version == "v1"


class TestPredictions:
    def test_returns_the_consensus_fair_probability(self) -> None:
        state = consensus((SHARP_A, 1.91, 1.95), (SHARP_B, 1.88, 1.98))
        predictions = MarketConsensusModel().predict(state)
        assert predictions[HOME].probability == pytest.approx(
            state.fair_probabilities[HOME], abs=1e-12
        )

    def test_probabilities_sum_to_one(self) -> None:
        state = consensus((SHARP_A, 1.91, 1.95), (SHARP_B, 1.88, 1.98))
        predictions = MarketConsensusModel().predict(state)
        assert sum(p.probability for p in predictions.values()) == pytest.approx(1.0, abs=1e-12)

    def test_its_edge_against_the_consensus_is_zero_by_construction(self) -> None:
        """La propiedad definitoria del baseline.

        Copia al mercado, así que no puede saber nada que el mercado no sepa. Todo
        lo que produzca en el pipeline vendrá del edge estructural — la diferencia
        entre el consenso y el mejor precio ejecutable.
        """
        from sportstar.core.edge import edge

        state = consensus((SHARP_A, 1.91, 1.95), (SHARP_B, 1.88, 1.98))
        predictions = MarketConsensusModel().predict(state)
        for selection_id, prediction in predictions.items():
            assert edge(prediction.probability, state.fair_probabilities[selection_id]) == (
                pytest.approx(0.0, abs=1e-12)
            )


class TestUncertainty:
    def test_uncertainty_comes_from_disagreement_between_sharp_books(self) -> None:
        agree = consensus((SHARP_A, 1.91, 1.95), (SHARP_B, 1.91, 1.95))
        disagree = consensus((SHARP_A, 1.70, 2.25), (SHARP_B, 2.15, 1.75))

        model = MarketConsensusModel()
        tight = model.predict(agree)[HOME]
        wide = model.predict(disagree)[HOME]

        assert tight.uncertainty is None  # dispersión 0: no hay intervalo que dar
        assert wide.uncertainty is not None and wide.uncertainty > 0

    def test_interval_brackets_the_point_estimate(self) -> None:
        state = consensus((SHARP_A, 1.70, 2.25), (SHARP_B, 2.15, 1.75))
        prediction = MarketConsensusModel().predict(state)[HOME]
        assert prediction.lower is not None and prediction.upper is not None
        assert prediction.lower < prediction.probability < prediction.upper

    def test_interval_stays_inside_the_open_unit_interval(self) -> None:
        # Un intervalo que toca 0 o 1 rompe cualquier conversión a cuota aguas abajo.
        state = consensus((SHARP_A, 1.02, 25.0), (SHARP_B, 1.30, 4.20))
        prediction = MarketConsensusModel().predict(state)[HOME]
        assert prediction.lower is not None and prediction.upper is not None
        assert prediction.lower > 0.0 and prediction.upper < 1.0


class TestModelPredictionValidation:
    def test_rejects_an_impossible_probability(self) -> None:
        from sportstar.core.errors import InvalidProbabilityError

        with pytest.raises(InvalidProbabilityError):
            ModelPrediction(1, 1.5, None, None, "m", "v1", T0)

    def test_rejects_an_inverted_interval(self) -> None:
        with pytest.raises(ValueError, match="intervalo invertido"):
            ModelPrediction(1, 0.5, 0.7, 0.3, "m", "v1", T0)

    def test_uncertainty_is_none_without_an_interval(self) -> None:
        assert ModelPrediction(1, 0.5, None, None, "m", "v1", T0).uncertainty is None
