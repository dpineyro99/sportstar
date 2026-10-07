"""Cálculo de rendimiento, con honestidad estadística incorporada.

Regla del módulo: **ninguna métrica se devuelve sin su tamaño de muestra**, y el
propio endpoint declara si esa muestra da para interpretarla.

No es escrupulosidad decorativa. Demostrar un ROI real del +3% exige del orden de
5.000-8.000 apuestas; un beat-close rate del 55%, unas 500-1.000. Con 90 apuestas
—un mes de MLB apostando el 15% del slate— el ROI es varianza con formato de
porcentaje, y presentarlo sin contexto es la forma más rápida de convencerse de
que una estrategia funciona cuando no hay evidencia de nada.

Por eso `model_beat_close_rate` se calcula sobre **todos los candidates** y no
solo sobre lo apostado: mide la misma señal con una muestra uno o dos órdenes de
magnitud mayor, y es lo que hace viable validar en semanas en vez de en
temporadas.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.betting import Bet, BetResult, Candidate
from ..db.enums import BetOutcome
from .schemas import PerformanceOut

# Muestras mínimas para que cada métrica signifique algo. Ver CHANGELOG, R8.
MIN_BETS_FOR_ROI = 5000
MIN_BETS_FOR_BEAT_CLOSE = 500
MIN_CANDIDATES_FOR_MODEL_SIGNAL = 1000

WINDOWS: dict[str, timedelta | None] = {
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "90d": timedelta(days=90),
    "all": None,
}


def _interpretation(n_bets: int, n_candidates: int) -> tuple[bool, str]:
    """Qué se puede leer con esta muestra, dicho explícitamente."""
    if n_bets == 0 and n_candidates == 0:
        return False, "Sin datos todavía."
    if n_bets >= MIN_BETS_FOR_ROI:
        return True, "Muestra suficiente para interpretar el ROI."
    if n_candidates >= MIN_CANDIDATES_FOR_MODEL_SIGNAL:
        return True, (
            f"El ROI no es interpretable con {n_bets} apuestas (hacen falta "
            f"~{MIN_BETS_FOR_ROI}), pero model_beat_close_rate sí lo es con "
            f"{n_candidates} candidates."
        )
    if n_bets >= MIN_BETS_FOR_BEAT_CLOSE:
        return False, (
            f"beat_close_rate empieza a ser legible con {n_bets} apuestas; el ROI "
            f"todavía no (hacen falta ~{MIN_BETS_FOR_ROI})."
        )
    return False, (
        f"Muestra insuficiente: {n_bets} apuestas y {n_candidates} candidates. "
        "El ROI a esta escala es varianza, no señal. Se muestra para seguimiento "
        "operativo, no para decidir."
    )


def compute_performance(
    session: Session,
    *,
    window: str = "30d",
    now: datetime | None = None,
) -> PerformanceOut:
    """Rendimiento agregado de una ventana temporal."""
    moment = now or datetime.now(UTC)
    delta = WINDOWS.get(window, WINDOWS["30d"])
    since = moment - delta if delta else None

    bets_query = select(Bet, BetResult).join(BetResult, BetResult.bet_id == Bet.id)
    if since is not None:
        bets_query = bets_query.where(Bet.placed_at >= since)
    rows = list(session.execute(bets_query))

    wins = sum(1 for _, r in rows if r.outcome is BetOutcome.WIN)
    losses = sum(1 for _, r in rows if r.outcome is BetOutcome.LOSS)
    pushes = sum(1 for _, r in rows if r.outcome is BetOutcome.PUSH)
    units_staked = sum(b.stake_units for b, _ in rows)
    units_won = sum(r.profit_units for _, r in rows)

    decided = wins + losses
    beat_close = [r.beat_closing_line for _, r in rows if r.beat_closing_line is not None]

    candidates_query = select(Candidate).where(Candidate.model_beat_close.is_not(None))
    if since is not None:
        candidates_query = candidates_query.where(Candidate.as_of >= since)
    scored = list(session.scalars(candidates_query))

    n_candidates = (
        session.scalar(
            select(func.count(Candidate.id)).where(
                *([Candidate.as_of >= since] if since is not None else [])
            )
        )
        or 0
    )

    interpretable, note = _interpretation(len(rows), len(scored))

    return PerformanceOut(
        window=window,
        group_by=None,
        n_bets=len(rows),
        n_candidates=n_candidates,
        wins=wins,
        losses=losses,
        pushes=pushes,
        units_staked=units_staked,
        units_won=units_won,
        roi=(units_won / units_staked) if units_staked else None,
        win_rate=(wins / decided) if decided else None,
        beat_close_rate=(sum(beat_close) / len(beat_close)) if beat_close else None,
        model_beat_close_rate=(
            sum(1 for c in scored if c.model_beat_close) / len(scored) if scored else None
        ),
        metrics_are_interpretable=interpretable,
        interpretation_note=note,
    )
