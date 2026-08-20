"""Carga de precios desde la base al formato del pipeline.

Cierra el ciclo: `odds_snapshots` -> `PricePoint` -> pipeline -> `candidates`.

Es la misma función para producción y para backtest. La diferencia es solo el
`as_of` que se le pasa, y eso no es casualidad: mantener dos caminos de carga
—uno para hoy y otro para el replay— garantiza que se desincronicen en la primera
semana y que el backtest deje de describir lo que el sistema haría de verdad.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.catalog import Sportsbook
from ..db.markets import OddsSnapshot, Selection
from .consensus import PricePoint


def load_price_points(
    session: Session,
    *,
    event_id: int,
    market_id: int,
    as_of: datetime | None = None,
) -> list[PricePoint]:
    """Precios de un mercado de un evento, con su `snapshot_id`.

    `as_of` recorta a lo observado hasta ese instante. Omitirlo trae todo el
    histórico, que es lo que necesitan el análisis de movimiento de línea y la
    captura de cierres.
    """
    query = (
        select(OddsSnapshot)
        .join(Selection, Selection.id == OddsSnapshot.selection_id)
        .where(Selection.event_id == event_id, Selection.market_id == market_id)
    )
    if as_of is not None:
        query = query.where(OddsSnapshot.captured_at <= as_of)

    return [
        PricePoint(
            selection_id=row.selection_id,
            sportsbook_id=row.sportsbook_id,
            price_decimal=row.price_decimal,
            captured_at=row.captured_at,
            line=row.line,
            is_available=row.is_available,
            snapshot_id=row.id,
        )
        for row in session.scalars(query)
    ]


def load_selection_ids(session: Session, *, event_id: int, market_id: int) -> tuple[int, ...]:
    """Selecciones de un mercado, en orden estable.

    El orden importa: `remove_vig` recibe una lista posicional, y un orden que
    cambie entre ejecuciones haría que el mismo mercado produjera fair
    probabilities distintas. Se ordena por id, que es inmutable.
    """
    return tuple(
        session.scalars(
            select(Selection.id)
            .where(Selection.event_id == event_id, Selection.market_id == market_id)
            .order_by(Selection.id)
        )
    )


def reference_book_ids(session: Session) -> set[int]:
    """Books que definen la probabilidad justa (sharp)."""
    return set(session.scalars(select(Sportsbook.id).where(Sportsbook.is_reference)))


def executable_book_ids(session: Session) -> set[int]:
    """Books donde realmente se puede apostar.

    Un precio mejor en un book inaccesible no es un edge, es una anécdota — por
    eso el conjunto sale de la base y no de una constante: cuando cambie a qué
    books tienes acceso, cambia una fila, no el código.
    """
    return set(session.scalars(select(Sportsbook.id).where(Sportsbook.is_executable)))


def book_names(session: Session) -> dict[int, str]:
    """Nombres por id, para las explicaciones de las recomendaciones."""
    return {row.id: row.name for row in session.scalars(select(Sportsbook))}
