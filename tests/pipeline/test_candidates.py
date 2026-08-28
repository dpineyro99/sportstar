"""Pipeline de candidates: consenso + modelo + precio -> recomendación.

Es el test de integración de Phase 2a: demuestra que el ciclo completo produce
apuestas con `market_consensus_v1`, es decir **sin ningún modelo estadístico**.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from sportstar.core.kelly import SizingMethod, StakeConfig
from sportstar.models import MarketConsensusModel
from sportstar.odds import PricePoint, consensus_fair_probabilities
from sportstar.pipeline import evaluate_market, evaluate_selection

T0 = datetime(2026, 8, 19, 18, 0, tzinfo=UTC)
HOME, AWAY = 1, 2
SHARP_A, SHARP_B = 10, 11
REC, REC2 = 20, 21
SELECTIONS = (HOME, AWAY)


def points(*books: tuple[int, float, float], at: datetime = T0) -> list[PricePoint]:
    out: list[PricePoint] = []
    for book, home, away in books:
        out += [PricePoint(HOME, book, home, at), PricePoint(AWAY, book, away, at)]
    return out


def setup(*, rec_home: float = 2.10, rec_away: float = 1.80, at: datetime = T0):
    """Consenso sharp equilibrado y un recreativo con precio generoso en HOME."""
    pts = points((SHARP_A, 1.91, 1.95), (SHARP_B, 1.90, 1.96), at=at) + points(
        (REC, rec_home, rec_away), at=at
    )
    consensus = consensus_fair_probabilities(pts, SELECTIONS, {SHARP_A, SHARP_B}, as_of=at)
    assert consensus is not None
    predictions = MarketConsensusModel().predict(consensus)
    return pts, consensus, predictions


class TestStructuralEdgeWithoutAModel:
    def test_the_market_baseline_produces_a_recommendation(self) -> None:
        """El objetivo de Phase 2a, verificado.

        El modelo copia al mercado: su edge de modelo es 0. La recomendación sale
        entera del edge estructural — el recreativo paga 2.10 donde el consenso
        sharp dice que lo justo son ~1.97.
        """
        pts, consensus, predictions = setup()
        results = evaluate_market(
            selections=SELECTIONS,
            consensus=consensus,
            predictions=predictions,
            points=pts,
            executable_book_ids={REC},
            as_of=T0,
        )
        best = results[0]
        assert best.selection_id == HOME
        assert best.is_recommended
        assert best.edge == pytest.approx(0.0, abs=1e-12)  # sin edge de modelo
        assert best.breakdown.structural_edge > 0.02
        assert best.total_edge == pytest.approx(best.breakdown.structural_edge, abs=1e-12)
        assert best.stake.units > 0

    def test_edge_decomposes_exactly(self) -> None:
        # total = modelo + estructural. La identidad que permite atribuir de dónde
        # vino la ventaja en vez de tener un solo número opaco.
        _, consensus, _ = setup()
        from sportstar.models.base import ModelPrediction

        prediction = ModelPrediction(HOME, 0.60, None, None, "m", "v1", T0)
        pts, consensus, _ = setup()
        result = evaluate_selection(
            selection_id=HOME,
            consensus=consensus,
            prediction=prediction,
            points=pts,
            executable_book_ids={REC},
            as_of=T0,
        )
        assert result is not None
        b = result.breakdown
        assert b.total_edge == pytest.approx(b.edge + b.structural_edge, abs=1e-12)

    def test_positive_total_edge_iff_positive_ev(self) -> None:
        pts, consensus, predictions = setup()
        for result in evaluate_market(
            selections=SELECTIONS,
            consensus=consensus,
            predictions=predictions,
            points=pts,
            executable_book_ids={REC},
            as_of=T0,
        ):
            assert (result.total_edge > 0) == (result.expected_value > 0)


class TestSeparationOfPrices:
    def test_a_better_executable_price_raises_ev_but_not_model_edge(self) -> None:
        """La separación de ARCHITECTURE §4.2 verificada de punta a punta.

        El edge de modelo depende del consenso; el EV, del precio que consigues.
        Mejorar el precio no puede hacerte creer que sabes más.
        """
        pts_a, consensus_a, preds_a = setup(rec_home=2.05)
        pts_b, consensus_b, preds_b = setup(rec_home=2.20)

        a = evaluate_selection(
            selection_id=HOME,
            consensus=consensus_a,
            prediction=preds_a[HOME],
            points=pts_a,
            executable_book_ids={REC},
            as_of=T0,
        )
        b = evaluate_selection(
            selection_id=HOME,
            consensus=consensus_b,
            prediction=preds_b[HOME],
            points=pts_b,
            executable_book_ids={REC},
            as_of=T0,
        )
        assert a is not None and b is not None
        assert b.expected_value > a.expected_value
        assert b.edge == pytest.approx(a.edge, abs=1e-12)

    def test_sharp_books_are_not_used_as_executable_prices(self) -> None:
        pts, consensus, predictions = setup()
        result = evaluate_selection(
            selection_id=HOME,
            consensus=consensus,
            prediction=predictions[HOME],
            points=pts,
            executable_book_ids={REC},
            as_of=T0,
        )
        assert result is not None
        assert result.best_price.sportsbook_id == REC


class TestRejections:
    def test_no_executable_price_yields_no_candidate(self) -> None:
        """Un candidate sin precio ejecutable no es una apuesta peor: no existe.

        Colarlo en las métricas contaminaría el denominador de todo.
        """
        pts, consensus, predictions = setup()
        assert (
            evaluate_selection(
                selection_id=HOME,
                consensus=consensus,
                prediction=predictions[HOME],
                points=pts,
                executable_book_ids={REC2},
                as_of=T0,
            )
            is None
        )

    def test_selection_outside_the_consensus_yields_no_candidate(self) -> None:
        pts, consensus, predictions = setup()
        assert (
            evaluate_selection(
                selection_id=999,
                consensus=consensus,
                prediction=predictions[HOME],
                points=pts,
                executable_book_ids={REC},
                as_of=T0,
            )
            is None
        )

    def test_stale_prices_are_persisted_as_candidates_but_not_recommended(self) -> None:
        # Se guarda igual: es lo que permite preguntar después qué habría pasado
        # con otro umbral de frescura.
        pts, consensus, predictions = setup(at=T0 - timedelta(hours=1))
        result = evaluate_selection(
            selection_id=HOME,
            consensus=consensus,
            prediction=predictions[HOME],
            points=pts,
            executable_book_ids={REC},
            as_of=T0,
        )
        assert result is not None
        assert not result.is_recommended
        assert "line_freshness" in result.filters.failed

    def test_rejected_candidates_get_no_stake(self) -> None:
        """Calcular un stake para algo que no se recomienda invitaría a leerlo
        como una sugerencia."""
        pts, consensus, predictions = setup(rec_home=1.85)  # sin ventaja
        result = evaluate_selection(
            selection_id=HOME,
            consensus=consensus,
            prediction=predictions[HOME],
            points=pts,
            executable_book_ids={REC},
            as_of=T0,
        )
        assert result is not None
        assert not result.is_recommended
        assert result.stake.units == 0.0


class TestOrderingAndSizing:
    def test_results_are_ordered_by_total_edge(self) -> None:
        pts, consensus, predictions = setup()
        results = evaluate_market(
            selections=SELECTIONS,
            consensus=consensus,
            predictions=predictions,
            points=pts,
            executable_book_ids={REC},
            as_of=T0,
        )
        assert [r.total_edge for r in results] == sorted(
            (r.total_edge for r in results), reverse=True
        )

    def test_stake_respects_the_configured_method(self) -> None:
        pts, consensus, predictions = setup()
        flat = evaluate_selection(
            selection_id=HOME,
            consensus=consensus,
            prediction=predictions[HOME],
            points=pts,
            executable_book_ids={REC},
            as_of=T0,
            stake_config=StakeConfig(method=SizingMethod.FLAT, flat_stake_units=2.0),
        )
        assert flat is not None
        assert flat.stake.units == 2.0
        assert flat.stake.method is SizingMethod.FLAT

    def test_confidence_is_computed_and_versioned(self) -> None:
        pts, consensus, predictions = setup()
        result = evaluate_selection(
            selection_id=HOME,
            consensus=consensus,
            prediction=predictions[HOME],
            points=pts,
            executable_book_ids={REC},
            as_of=T0,
        )
        assert result is not None
        assert 0.0 <= result.confidence.score <= 10.0
        assert result.confidence.version == 0


class TestMarketEvaluation:
    def test_selections_without_a_prediction_are_skipped(self) -> None:
        # El modelo puede no cubrir todos los lados de un mercado. Se omiten en
        # vez de fabricar una probabilidad.
        pts, consensus, predictions = setup()
        partial = {HOME: predictions[HOME]}
        results = evaluate_market(
            selections=SELECTIONS,
            consensus=consensus,
            predictions=partial,
            points=pts,
            executable_book_ids={REC},
            as_of=T0,
        )
        assert [r.selection_id for r in results] == [HOME]
