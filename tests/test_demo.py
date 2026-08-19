"""La demo del pipeline.

Vale como test de humo del cableado completo: si el ciclo consenso -> modelo ->
edge -> filtros -> stake se rompe en cualquier punto, esto falla.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sportstar.demo import EXECUTABLE_BOOKS, REFERENCE_BOOKS, build_market, render_card, run_demo
from sportstar.models import MarketConsensusModel
from sportstar.odds import consensus_fair_probabilities
from sportstar.pipeline import evaluate_market


def evaluations():
    now = datetime(2026, 8, 19, 18, 0, tzinfo=UTC)
    points, selections, labels = build_market(now)
    consensus = consensus_fair_probabilities(points, selections, REFERENCE_BOOKS, as_of=now)
    assert consensus is not None
    predictions = MarketConsensusModel().predict(consensus, now)
    results = evaluate_market(
        selections=selections,
        consensus=consensus,
        predictions=predictions,
        points=points,
        executable_book_ids=EXECUTABLE_BOOKS,
        as_of=now,
    )
    return results, labels


def test_demo_runs_and_exits_cleanly(capsys) -> None:
    assert run_demo() == 0
    assert "BEST BET" in capsys.readouterr().out


def test_demo_produces_exactly_one_recommendation() -> None:
    # Un mercado de dos lados no puede tener ventaja en ambos: si sale más de
    # una recomendación, el vig no se está retirando bien.
    results, _ = evaluations()
    assert len([r for r in results if r.is_recommended]) == 1


def test_the_recommendation_comes_entirely_from_structural_edge() -> None:
    """Lo que Phase 2a demuestra: apuestas sin modelo estadístico.

    El baseline copia al mercado, así que su edge de modelo es 0. La ventaja sale
    de que un book recreativo paga más de lo que el consenso sharp considera justo.
    """
    results, _ = evaluations()
    best = next(r for r in results if r.is_recommended)
    assert best.edge == 0.0
    assert best.breakdown.structural_edge > 0.02
    assert best.total_edge == best.breakdown.structural_edge


def test_the_best_price_comes_from_an_executable_book() -> None:
    results, _ = evaluations()
    best = next(r for r in results if r.is_recommended)
    assert best.best_price.sportsbook_id in EXECUTABLE_BOOKS


def test_card_reports_missing_signals_instead_of_hiding_them(capsys) -> None:
    # Sin histórico no hay calibración ni tamaño de muestra. La tarjeta lo dice
    # en vez de presentar un número como si estuviera plenamente informado.
    results, labels = evaluations()
    card = render_card(results[0], labels[results[0].selection_id])
    assert "Señales ausentes" in card
    assert "historical_calibration" in card
