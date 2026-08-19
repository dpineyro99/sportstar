"""Demostración del pipeline con precios sintéticos.

Existe para que el ciclo completo —consenso sharp, modelo, edge, EV, filtros,
stake— se pueda ver funcionando **hoy**, sin proveedores de datos ni API keys.

Los precios están escritos a mano, no obtenidos de ningún sitio. No demuestra que
el sistema gane dinero; demuestra que el cableado está bien y enseña el formato
de salida al que apunta el dashboard.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from .core.odds import decimal_to_american
from .models import MarketConsensusModel
from .odds import PricePoint, consensus_fair_probabilities
from .pipeline import CandidateEvaluation, evaluate_market

PINNACLE, CIRCA = 1, 2
DRAFTKINGS, FANDUEL = 4, 5
REFERENCE_BOOKS = {PINNACLE, CIRCA}
EXECUTABLE_BOOKS = {DRAFTKINGS, FANDUEL}

BOOK_NAMES = {PINNACLE: "Pinnacle", CIRCA: "Circa", DRAFTKINGS: "DraftKings", FANDUEL: "FanDuel"}


def build_market(now: datetime) -> tuple[list[PricePoint], tuple[int, int], dict[int, str]]:
    """NYY vs BOS. Los sharp coinciden; DraftKings paga de más por los Yankees."""
    home, away = 101, 102
    labels = {home: "NYY Yankees ML", away: "BOS Red Sox ML"}
    fresh = now - timedelta(seconds=45)

    points = [
        # Books de referencia: definen la probabilidad justa.
        PricePoint(home, PINNACLE, 1.92, fresh),
        PricePoint(away, PINNACLE, 1.98, fresh),
        PricePoint(home, CIRCA, 1.93, fresh),
        PricePoint(away, CIRCA, 1.96, fresh),
        # Books ejecutables: definen el precio que consigues.
        PricePoint(home, DRAFTKINGS, 2.15, fresh),
        PricePoint(away, DRAFTKINGS, 1.75, fresh),
        PricePoint(home, FANDUEL, 2.05, fresh),
        PricePoint(away, FANDUEL, 1.80, fresh),
    ]
    return points, (home, away), labels


def render_card(evaluation: CandidateEvaluation, label: str) -> str:
    """Tarjeta al estilo del BEST BET del brief."""
    b = evaluation.breakdown
    american = decimal_to_american(b.best_decimal)
    book = BOOK_NAMES.get(evaluation.best_price.sportsbook_id, "?")

    lines = [
        "🔥 BEST BET" if evaluation.is_recommended else "   CANDIDATE (no recomendado)",
        "",
        f"   {label}   {american:+.0f}   @ {book}",
        "",
        f"   Model probability : {evaluation.prediction.probability:>7.1%}",
        f"   Market fair       : {b.market_fair_prob:>7.1%}",
        f"   Market break-even : {b.break_even_prob:>7.1%}",
        "",
        f"   EDGE (total)      : {b.total_edge:>+7.2%}",
        f"     de modelo       : {b.edge:>+7.2%}",
        f"     estructural     : {b.structural_edge:>+7.2%}",
        f"   EXPECTED ROI      : {b.expected_value:>+7.2%}",
        f"   CONFIDENCE        : {evaluation.confidence.score:>7.1f} / 10",
        f"   STAKE             : {evaluation.stake.units:>7.2f} units",
    ]
    if not evaluation.is_recommended:
        lines += ["", f"   Rechazado por: {', '.join(evaluation.filters.failed)}"]
    if evaluation.confidence.missing_components:
        lines += [
            "",
            f"   Señales ausentes: {', '.join(evaluation.confidence.missing_components)}",
        ]
    return "\n".join(lines)


def run_demo() -> int:
    now = datetime.now(UTC)
    points, selections, labels = build_market(now)

    consensus = consensus_fair_probabilities(points, selections, REFERENCE_BOOKS, as_of=now)
    if consensus is None:
        print("Sin consenso: ningún book de referencia tiene el mercado completo.")
        return 1

    model = MarketConsensusModel()
    predictions = model.predict(consensus, now)

    print("=" * 62)
    print("  DEMO — pipeline completo con precios sintéticos")
    print("=" * 62)
    print(f"\n  Modelo    : {model.name}_{model.version}")
    print(f"  Referencia: {', '.join(BOOK_NAMES[b] for b in consensus.books_used)}")
    print(f"  Ejecución : {', '.join(BOOK_NAMES[b] for b in sorted(EXECUTABLE_BOOKS))}")
    print("\n  El modelo copia al mercado: su edge de modelo es 0 por construcción.")
    print("  Todo lo que aparezca abajo viene del edge estructural.\n")

    results = evaluate_market(
        selections=selections,
        consensus=consensus,
        predictions=predictions,
        points=points,
        executable_book_ids=EXECUTABLE_BOOKS,
        as_of=now,
    )

    for evaluation in results:
        print(render_card(evaluation, labels[evaluation.selection_id]))
        print("\n" + "-" * 62 + "\n")

    recommended = [r for r in results if r.is_recommended]
    print(f"  {len(recommended)} de {len(results)} candidates superaron los filtros.")
    return 0
