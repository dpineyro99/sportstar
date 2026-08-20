"""Agregación de snapshots de odds: consenso, mejor precio, cierre, movimiento.

**El orden de las operaciones importa y es fácil equivocarse.** Para obtener la
probabilidad justa de consenso hay que:

1. tomar, de cada book, **todos** los lados del mercado en el mismo instante;
2. retirarle el vig a cada book **por separado**;
3. promediar las probabilidades ya limpias entre books.

Hacerlo al revés — promediar las implied con vig y quitar el vig al final — da un
resultado distinto y sesgado, porque cada book carga un margen distinto y el
promedio lo mezcla con la señal. Es el tipo de error que no rompe nada, solo
desplaza todos los edges en la misma dirección.

Estas funciones son puras y trabajan sobre `PricePoint`, no sobre filas de la
base: así se pueden testear con mercados construidos a mano y se reutilizan tal
cual en el backtest, que reconstruye el estado del mundo en un instante pasado.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from ..core.novig import NoVigMethod, remove_vig
from ..core.odds import decimal_to_implied


@dataclass(frozen=True, slots=True)
class PricePoint:
    """Un precio observado. Espejo ligero de `OddsSnapshot`, sin la base.

    `snapshot_id` es el puente con la persistencia: al cargar desde la base lleva
    el id de la fila, y así el candidate puede referenciar exactamente qué
    snapshots entraron en su consenso. En precios sintéticos (demo, tests) es
    `None`, y persistir uno así es un error explícito, no un `NULL` silencioso.
    """

    selection_id: int
    sportsbook_id: int
    price_decimal: float
    captured_at: datetime
    line: float = 0.0
    is_available: bool = True
    snapshot_id: int | None = None


@dataclass(frozen=True, slots=True)
class ConsensusResult:
    """Probabilidad justa de consenso para un mercado, en un instante.

    `per_book` conserva el desglose porque la **dispersión entre books de
    referencia** es información, no ruido intermedio: cuando los sharp discrepan,
    el mercado está menos seguro, y eso debe bajar la confianza de cualquier
    recomendación construida sobre ese precio.
    """

    fair_probabilities: dict[int, float]
    # Implícitas CON vig promediadas entre books de referencia. Su diferencia con
    # `fair_probabilities` es el margen que carga el mercado sharp en ese lado —
    # información real, no un duplicado de la fair.
    implied_probabilities: dict[int, float]
    per_book: tuple[tuple[int, dict[int, float]], ...]
    method: NoVigMethod
    as_of: datetime
    # Snapshots que entraron en el promedio. Es lo que permite reconstruir el
    # consenso exacto de una apuesta histórica meses después.
    contributing_snapshot_ids: tuple[int, ...] = ()

    @property
    def books_used(self) -> tuple[int, ...]:
        return tuple(book_id for book_id, _ in self.per_book)

    @property
    def book_count(self) -> int:
        return len(self.per_book)

    def dispersion(self, selection_id: int) -> float:
        """Desviación típica poblacional de la fair probability entre books.

        0.0 con un solo book: no es que haya acuerdo, es que no hay con quién
        discrepar. Quien la consuma debe distinguir ambos casos mirando
        `book_count` — por eso no se devuelve `None`, que se colaría en una
        comparación numérica sin avisar.
        """
        values = [probs[selection_id] for _, probs in self.per_book if selection_id in probs]
        if len(values) < 2:
            return 0.0
        mean = sum(values) / len(values)
        variance = sum((v - mean) ** 2 for v in values) / len(values)
        # float() explícito: para mypy `float ** float` es Any, porque una base
        # negativa con exponente fraccionario daría complex. La varianza nunca
        # es negativa, así que el resultado es real.
        return float(variance**0.5)


def latest_per_book_selection(
    points: list[PricePoint], as_of: datetime | None = None
) -> dict[tuple[int, int], PricePoint]:
    """Último precio de cada `(book, selection)` no posterior a `as_of`.

    Filtrar por `as_of` no es un detalle: es lo que permite que el backtest use
    exactamente esta función y no una variante paralela que se desincroniza.
    """
    latest: dict[tuple[int, int], PricePoint] = {}
    for point in points:
        if as_of is not None and point.captured_at > as_of:
            continue
        key = (point.sportsbook_id, point.selection_id)
        current = latest.get(key)
        if current is None or point.captured_at > current.captured_at:
            latest[key] = point
    return latest


def market_state(
    points: list[PricePoint], as_of: datetime | None = None
) -> dict[int, dict[int, PricePoint]]:
    """Estado del mercado en `as_of`: `{book_id: {selection_id: PricePoint}}`."""
    state: dict[int, dict[int, PricePoint]] = defaultdict(dict)
    for (book_id, selection_id), point in latest_per_book_selection(points, as_of).items():
        if point.is_available:
            state[book_id][selection_id] = point
    return dict(state)


def book_fair_probabilities(
    book_prices: dict[int, PricePoint],
    selections: tuple[int, ...],
    method: NoVigMethod = NoVigMethod.PROPORTIONAL,
) -> dict[int, float] | None:
    """Probabilidades sin vig de **un** book, o `None` si le falta algún lado.

    Devolver `None` en vez de estimar con los lados disponibles es deliberado: sin
    el mercado completo no se puede saber cuánto margen lleva el precio, y
    rellenarlo con una constante sería una invención que llega hasta el edge.
    """
    if not all(sel in book_prices for sel in selections):
        return None
    implied = [decimal_to_implied(book_prices[sel].price_decimal) for sel in selections]
    try:
        fair = remove_vig(implied, method)
    except Exception:
        # Un book con overround <= 1 tiene un precio corrupto o lados mal
        # emparejados. Se excluye del consenso en vez de contaminarlo.
        return None
    return dict(zip(selections, fair, strict=True))


def consensus_fair_probabilities(
    points: list[PricePoint],
    selections: tuple[int, ...],
    reference_book_ids: set[int],
    *,
    as_of: datetime,
    method: NoVigMethod = NoVigMethod.PROPORTIONAL,
) -> ConsensusResult | None:
    """Consenso de books de referencia: quita el vig por book y luego promedia.

    Solo entran books de referencia (sharp). Los recreativos definen el precio
    que conseguimos, no la probabilidad justa contra la que medimos el edge:
    incluirlos en el consenso equivaldría a comparar el mercado consigo mismo.

    Peso igual para todos los books de referencia. Ponderar por calidad de book es
    una pregunta de calibración de Phase 3 — inventar pesos ahora sería
    exactamente la fórmula arbitraria que el brief pide evitar.
    """
    state = market_state(points, as_of)
    per_book: list[dict[int, float]] = []
    per_book_implied: list[dict[int, float]] = []
    books_used: list[int] = []
    snapshot_ids: list[int] = []

    for book_id in sorted(state):
        if book_id not in reference_book_ids:
            continue
        fair = book_fair_probabilities(state[book_id], selections, method)
        if fair is not None:
            per_book.append(fair)
            per_book_implied.append(
                {sel: decimal_to_implied(state[book_id][sel].price_decimal) for sel in selections}
            )
            books_used.append(book_id)
            snapshot_ids.extend(
                snapshot_id
                for sel in selections
                if (snapshot_id := state[book_id][sel].snapshot_id) is not None
            )

    if not per_book:
        return None

    # La media aritmética de conjuntos que suman 1 también suma 1; se renormaliza
    # solo para absorber el error de coma flotante.
    averaged = {sel: sum(book[sel] for book in per_book) / len(per_book) for sel in selections}
    total = sum(averaged.values())
    implied = {
        sel: sum(book[sel] for book in per_book_implied) / len(per_book_implied)
        for sel in selections
    }
    return ConsensusResult(
        fair_probabilities={sel: p / total for sel, p in averaged.items()},
        implied_probabilities=implied,
        per_book=tuple(zip(books_used, per_book, strict=True)),
        method=method,
        as_of=as_of,
        contributing_snapshot_ids=tuple(snapshot_ids),
    )


def best_available(
    points: list[PricePoint],
    selection_id: int,
    executable_book_ids: set[int],
    *,
    as_of: datetime,
) -> PricePoint | None:
    """Mejor precio ejecutable para una selección: la cuota decimal más alta.

    Solo books ejecutables. Un precio mejor en un book donde no puedes apostar no
    es un edge, es una anécdota.

    Ante empate gana el book de id menor, para que el resultado sea determinista
    y el backtest reproducible.
    """
    state = market_state(points, as_of)
    candidates = [
        prices[selection_id]
        for book_id, prices in state.items()
        if book_id in executable_book_ids and selection_id in prices
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda p: (p.price_decimal, -p.sportsbook_id))


def closing_points(points: list[PricePoint], start_time: datetime) -> list[PricePoint]:
    """Últimos precios estrictamente anteriores al inicio del evento.

    Estrictamente anteriores: un precio capturado en el instante del primer
    lanzamiento ya puede reflejar lo que pasa en el campo.
    """
    before_start = [p for p in points if p.captured_at < start_time]
    return list(latest_per_book_selection(before_start).values())


def opening_points(points: list[PricePoint]) -> list[PricePoint]:
    """Primer precio observado de cada `(book, selection)`."""
    earliest: dict[tuple[int, int], PricePoint] = {}
    for point in points:
        key = (point.sportsbook_id, point.selection_id)
        current = earliest.get(key)
        if current is None or point.captured_at < current.captured_at:
            earliest[key] = point
    return list(earliest.values())


def line_movement(
    points: list[PricePoint], selection_id: int, sportsbook_id: int
) -> list[PricePoint]:
    """Serie temporal ordenada de una selección en un book."""
    return sorted(
        (p for p in points if p.selection_id == selection_id and p.sportsbook_id == sportsbook_id),
        key=lambda p: p.captured_at,
    )


def line_age_seconds(point: PricePoint, as_of: datetime) -> float:
    """Antigüedad de un precio. Alimenta el filtro de frescura.

    Un snapshot de hace 40 minutos produce edge fantasma: el precio que creemos
    tener ya no está disponible, y el backtest que lo usa no se reproduce en
    paper trading.
    """
    return float((as_of - point.captured_at).total_seconds())
