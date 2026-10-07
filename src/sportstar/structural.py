"""Edge estructural: ¿alguna casa ejecutable paga mejor de lo que vale?

Qué mide
--------
Las dos fuentes de edge del sistema son distintas y se miden distinto:

- **edge de modelo** = `model_prob - fair_prob`. Requiere un modelo que sepa algo
  que el mercado no. Phase 3 y Phase 2b lo midieron: en el moneyline de MLB, a
  efectos prácticos, no existe.
- **edge estructural** = `fair_prob - implied(mejor precio ejecutable)`. No
  requiere modelo ninguno. Solo requiere que dos casas discrepen lo bastante como
  para que una pague por encima de lo que el consenso dice que vale.

Esta es la segunda. No necesita predecir nada: necesita que el mercado no esté
perfectamente arbitrado entre casas.

Cómo se mide sin engañarse
--------------------------
Tres cosas, y las tres cambian el número:

1. **La casa evaluada no puede entrar en el consenso contra el que se la mide.**
   Si entra, arrastra la referencia hacia su propio precio y el edge medido se
   encoge sin que nada lo delate.
2. **Deduplicar operadores.** Dos marcas de la misma casa no son un consenso de
   dos. Medido: LowVig.ag y BetOnline.ag coincidieron en 26 de 28 precios.
3. **El vig va dentro.** El edge se mide contra `implied(precio)`, que ya incluye
   el margen de la casa. Un edge positivo significa que la casa paga por encima de
   lo justo **después** de cobrarse su margen — que es lo que lo hace real, y lo
   que hace que casi siempre salga negativo.

Dos anclas para el "precio justo", y por qué hay dos
----------------------------------------------------
- **`REFERENCE`** — el consenso lo forman solo los books de referencia (sharp) y
  se evalúan solo los ejecutables. Es la definición del pipeline en vivo. Su
  problema es práctico: en el feed de `regions=us` los sharp son dos operadores y
  no aparecen en todos los eventos, así que el ancla es débil y se pierde muestra.
- **`LEAVE_ONE_OUT`** — para cada casa, el consenso se calcula con **todas las
  demás**, y se comprueba si esa casa paga por encima. Usa el feed entero en vez
  de dos operadores, y no obliga a decidir quién es sharp.

La segunda no es un atajo, es la corrección del sesgo del punto 1 llevada a su
conclusión: si sacar a la casa evaluada del consenso es lo que hace honesta la
comparación, entonces se puede usar **todo** el resto del feed sin contaminarla.

Dos filtros que no son opcionales
---------------------------------
Se aprendieron midiendo. La primera versión de este módulo no los tenía y produjo
un edge de **+8,26%** que resultó ser basura:

- **Solo pre-partido.** Un evento ya empezado tiene odds *in-play*, que responden
  al marcador y no al precio justo del partido completo. Medir edge estructural
  contra ellas no mide nada. El caso real: Brewers @ Padres empezó a las 01:40Z y
  los precios eran de las 03:26Z.
- **Solo líneas frescas.** `last_update` por casa dice cuándo movió cada una. Una
  casa que no se ha movido mientras el resto sí tiene un precio que parece
  generoso y no lo es. En ese mismo partido, MyBookie.ag marcaba -154 mientras las
  otras cinco estaban entre -250 y -295.

La frescura se mide **relativa al precio más reciente del propio mercado**, no
contra el reloj: lo que importa es si esa casa se quedó atrás respecto a las
demás, y eso no depende de cuándo se descargó el feed.

Estos dos filtros son los gates `line_freshness` y `check_odds_after_start` del
pipeline en vivo. Que su ausencia aquí produjese justo el tipo de número que
alguien querría creer es la mejor demostración de para qué existen.

Lo que este módulo NO puede decir
---------------------------------
Nada sobre frecuencia a partir de una sola jornada. Un edge que aparece en el 0,5%
de las selecciones necesita miles de observaciones para distinguirse de cero, y
una jornada de MLB son ~30. El informe lo dice en cada ejecución en vez de dejar
que el lector saque la conclusión que le apetezca.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum

from .core.errors import CoreError
from .core.novig import NoVigMethod, remove_vig
from .core.odds import american_to_decimal, decimal_to_implied

#: Umbral de edge del filtro en vivo (`filters/gates.MIN_EDGE`). Se repite aquí
#: para que el informe cuente cuántas selecciones lo superarían de verdad.
LIVE_EDGE_THRESHOLD = 0.02

#: Operadores mínimos en un consenso para que sea un consenso. Con uno solo no hay
#: acuerdo, hay una opinión.
MIN_CONSENSUS_OPERATORS = 2

#: Cuánto puede quedarse atrás una casa respecto al precio más reciente del mismo
#: mercado antes de considerarla stale. Generoso a propósito: el objetivo es cazar
#: la casa que lleva una hora sin mirar, no afinar milisegundos.
MAX_STALENESS = timedelta(minutes=10)


class Anchor(StrEnum):
    """Contra qué se mide el precio justo. Ver el docstring del módulo."""

    REFERENCE = "reference"
    LEAVE_ONE_OUT = "leave_one_out"


@dataclass(frozen=True, slots=True)
class BookPrice:
    """Un precio de una casa para un lado de un mercado.

    Se guarda en decimal aunque The Odds API lo sirva en americano: el americano no
    es ordenable —+150 paga más que -110, pero comparar los enteros da lo
    contrario— y todo lo que hace este módulo es comparar precios.
    """

    book_key: str
    operator: str
    side: str
    price_decimal: float
    last_update: datetime | None = None

    @property
    def implied(self) -> float:
        return decimal_to_implied(self.price_decimal)


@dataclass(frozen=True, slots=True)
class SelectionEdge:
    """El edge estructural de un lado concreto de un mercado concreto."""

    event_id: str
    home_team: str
    away_team: str
    side: str
    anchor: Anchor
    fair_probability: float
    best_price_decimal: float
    best_book: str
    worst_price_decimal: float
    consensus_operators: int

    @property
    def structural_edge(self) -> float:
        """`fair - implied(mejor precio)`. Positivo = la casa paga de más."""
        return self.fair_probability - decimal_to_implied(self.best_price_decimal)

    @property
    def price_dispersion(self) -> float:
        """Cuánto separa al mejor precio del peor, en cuota relativa.

        Es la materia prima del edge estructural: sin dispersión entre casas no hay
        nada que arbitrar, por bueno que sea el consenso.
        """
        return self.best_price_decimal / self.worst_price_decimal - 1.0


@dataclass(frozen=True, slots=True)
class StructuralReport:
    """Lo que se puede afirmar —y lo que no— sobre un conjunto de jornadas."""

    anchor: Anchor
    edges: list[SelectionEdge]
    n_events: int
    n_selections: int
    n_no_consensus: int
    n_no_price: int
    #: Eventos descartados por haber empezado ya. Sus odds son in-play y no miden
    #: el precio justo del partido completo.
    n_in_play: int = 0
    #: Precios descartados por llevar demasiado sin moverse respecto al mercado.
    n_stale_prices: int = 0
    observed_at: datetime | None = None
    #: Casas vistas en el feed que el catálogo no conoce. Un book nuevo puede ser
    #: un sharp que deberíamos estar usando; enterarse tarde es caro.
    unknown_books: set[str] = field(default_factory=set)

    def above(self, threshold: float) -> list[SelectionEdge]:
        return [e for e in self.edges if e.structural_edge >= threshold]

    def rule_of_three_upper_bound(self) -> float | None:
        """Cota superior al 95% de la frecuencia real cuando no se observó ninguno.

        Con 0 éxitos en n intentos, la cota es ~3/n. Es la cifra que convierte
        "no encontré nada" en algo cuantificado, y casi siempre revela que la
        muestra no permite concluir nada.
        """
        if not self.edges or self.above(LIVE_EDGE_THRESHOLD):
            return None
        return 3.0 / len(self.edges)

    def summary(self) -> str:
        head = (
            f"ancla {self.anchor.value}   eventos {self.n_events}   "
            f"selecciones {self.n_selections}   evaluables {len(self.edges)}\n"
            f"  descartadas: sin consenso {self.n_no_consensus}, "
            f"sin precio {self.n_no_price}\n"
            f"  filtrados: {self.n_in_play} eventos ya empezados, "
            f"{self.n_stale_prices} precios stale"
        )
        if not self.edges:
            return head

        values = sorted(e.structural_edge for e in self.edges)
        dispersions = sorted(e.price_dispersion for e in self.edges)
        n = len(values)
        positive = sum(1 for v in values if v > 0)
        over = sum(1 for v in values if v >= LIVE_EDGE_THRESHOLD)
        lines = [
            head,
            "",
            "--- edge estructural (fair - implied(mejor precio)) ---",
            f"  media    {sum(values) / n * 100:+.3f}%",
            f"  mediana  {values[n // 2] * 100:+.3f}%",
            f"  p90      {values[min(n - 1, int(n * 0.90))] * 100:+.3f}%",
            f"  máximo   {values[-1] * 100:+.3f}%",
            f"  > 0%     {positive} de {n} ({positive / n:.1%})",
            f"  >= {LIVE_EDGE_THRESHOLD:.0%}     {over} de {n}",
            "",
            "--- dispersión entre casas (mejor/peor - 1) ---",
            f"  mediana  {dispersions[n // 2] * 100:.2f}%",
            f"  p90      {dispersions[min(n - 1, int(n * 0.90))] * 100:.2f}%",
            f"  máximo   {dispersions[-1] * 100:.2f}%",
        ]
        bound = self.rule_of_three_upper_bound()
        if bound is not None:
            lines += [
                "",
                f"  ⚠️  cero por encima del {LIVE_EDGE_THRESHOLD:.0%} en {n} "
                f"observaciones. La regla de tres deja la cota superior al 95% en",
                f"      {bound:.1%}: con esta muestra no se distingue "
                '"nunca" de "una de cada ' + f'{1 / bound:.0f}".',
            ]
        if self.unknown_books:
            lines += ["", f"  casas fuera del catálogo: {sorted(self.unknown_books)}"]
        return "\n".join(lines)


def _items(value: object) -> list[object]:
    """Lista o nada. El payload viene del exterior y no se le presume forma."""
    return value if isinstance(value, list) else []


def _parse_time(value: object) -> datetime | None:
    """ISO-8601 con `Z`, o `None`. No se presume forma a lo que viene de fuera."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _prices_by_event(
    payload: list[dict[str, object]],
    operators: dict[str, str],
) -> dict[str, tuple[dict[str, object], list[BookPrice]]]:
    """Aplana el payload a precios por evento. Solo moneyline (`h2h`)."""
    out: dict[str, tuple[dict[str, object], list[BookPrice]]] = {}
    for event in payload:
        if not isinstance(event, dict):
            continue
        prices: list[BookPrice] = []
        for book in _items(event.get("bookmakers")):
            if not isinstance(book, dict):
                continue
            key = str(book.get("key", ""))
            for market in _items(book.get("markets")):
                if not isinstance(market, dict) or market.get("key") != "h2h":
                    continue
                updated = _parse_time(market.get("last_update")) or _parse_time(
                    book.get("last_update")
                )
                for outcome in _items(market.get("outcomes")):
                    if not isinstance(outcome, dict):
                        continue
                    price = outcome.get("price")
                    if not isinstance(price, (int, float)) or isinstance(price, bool):
                        continue
                    prices.append(
                        BookPrice(
                            book_key=key,
                            operator=operators.get(key, key),
                            side=str(outcome.get("name", "")),
                            price_decimal=american_to_decimal(float(price)),
                            last_update=updated,
                        )
                    )
        out[str(event.get("id", ""))] = (event, prices)
    return out


def fresh_prices(
    prices: list[BookPrice], *, max_staleness: timedelta = MAX_STALENESS
) -> list[BookPrice]:
    """Descarta las casas que se quedaron atrás respecto al mercado.

    La frescura se mide **relativa al precio más reciente del propio mercado**, no
    contra el reloj: lo que importa es si una casa se quedó atrás mientras las
    demás se movían, y eso no depende de cuándo se descargó el feed.

    Una casa sin `last_update` se conserva: descartarla sería castigar un hueco del
    proveedor, no una línea vieja. Si ninguna lo trae, no hay nada que comparar y
    pasan todas.
    """
    stamps = [p.last_update for p in prices if p.last_update is not None]
    if not stamps:
        return prices
    newest = max(stamps)
    return [p for p in prices if p.last_update is None or newest - p.last_update <= max_staleness]


def _consensus(
    prices: list[BookPrice],
    sides: tuple[str, str],
    include: set[str],
    method: NoVigMethod,
) -> tuple[dict[str, float], int] | None:
    """Quita el vig a cada casa y **luego** promedia. Nunca al revés.

    Promediar las implied con vig y quitar el vig al final da un resultado distinto
    y sesgado: cada casa carga un margen distinto y el promedio lo mezcla con la
    señal. Es el tipo de error que no rompe nada, solo desplaza todos los edges en
    la misma dirección.

    Deduplica por operador conservando la clave menor, para que el resultado sea
    determinista.
    """
    by_book: dict[str, dict[str, float]] = defaultdict(dict)
    operator_of: dict[str, str] = {}
    for price in prices:
        if price.book_key not in include:
            continue
        by_book[price.book_key][price.side] = price.implied
        operator_of[price.book_key] = price.operator

    per_book: list[dict[str, float]] = []
    seen: set[str] = set()
    for book_key in sorted(by_book):
        implied = by_book[book_key]
        if set(implied) != set(sides):
            continue
        operator = operator_of[book_key]
        if operator in seen:
            continue
        try:
            fair = remove_vig([implied[sides[0]], implied[sides[1]]], method=method)
        except CoreError:
            # Mercado incoherente: overround <= 1, o un precio imposible. Con
            # precios reales eso casi nunca es un arbitraje, es un precio corrupto
            # o dos lados que no son del mismo mercado. Se descarta **esa casa** y
            # sigue el resto: un precio malo no puede tumbar el análisis entero.
            continue
        seen.add(operator)
        per_book.append({sides[0]: fair[0], sides[1]: fair[1]})

    if len(per_book) < MIN_CONSENSUS_OPERATORS:
        return None
    averaged = {side: sum(b[side] for b in per_book) / len(per_book) for side in sides}
    total = sum(averaged.values())
    return {side: p / total for side, p in averaged.items()}, len(per_book)


def analyze(
    payload: list[dict[str, object]],
    *,
    reference_books: set[str],
    executable_books: set[str],
    operators: dict[str, str],
    anchor: Anchor = Anchor.LEAVE_ONE_OUT,
    method: NoVigMethod = NoVigMethod.PROPORTIONAL,
    observed_at: datetime | None = None,
    max_staleness: timedelta = MAX_STALENESS,
    pregame_only: bool = True,
) -> StructuralReport:
    """Mide el edge estructural de cada lado de cada evento.

    Con `REFERENCE`, los dos conjuntos **deben ser disjuntos**: si se solapan, la
    casa evaluada contamina la referencia contra la que se la evalúa.

    Con `LEAVE_ONE_OUT` el solapamiento es irrelevante —cada casa se compara contra
    el resto— y `reference_books` solo sirve para ampliar el universo de casas que
    entran en el consenso.
    """
    if anchor is Anchor.REFERENCE and (overlap := reference_books & executable_books):
        raise ValueError(
            f"books a la vez de referencia y ejecutables: {sorted(overlap)}. "
            "Una casa que entra en el consenso contra el que se la compara arrastra "
            "la referencia hacia su propio precio, y el edge medido se encoge sin "
            "que nada lo delate. Con anchor=LEAVE_ONE_OUT esto no es un problema."
        )

    edges: list[SelectionEdge] = []
    n_selections = no_consensus = no_price = in_play = stale = 0
    unknown: set[str] = set()
    known = reference_books | executable_books

    for event_id, (event, raw_prices) in _prices_by_event(payload, operators).items():
        unknown |= {p.book_key for p in raw_prices} - known

        # Un evento ya empezado tiene odds in-play: responden al marcador, no al
        # precio justo del partido. Incluirlas no mide edge estructural.
        start = _parse_time(event.get("commence_time"))
        if pregame_only and start is not None:
            reference_now = observed_at or max(
                (p.last_update for p in raw_prices if p.last_update is not None),
                default=None,
            )
            if reference_now is not None and reference_now >= start:
                in_play += 1
                continue

        prices = fresh_prices(raw_prices, max_staleness=max_staleness)
        stale += len(raw_prices) - len(prices)

        sides = tuple(sorted({p.side for p in prices}))
        if len(sides) != 2:
            continue
        pair = (sides[0], sides[1])
        n_selections += 2
        home, away = str(event.get("home_team", "")), str(event.get("away_team", ""))

        for side in pair:
            candidates = [p for p in prices if p.side == side and p.book_key in executable_books]
            if not candidates:
                no_price += 1
                continue
            best = max(candidates, key=lambda p: (p.price_decimal, p.book_key))
            worst = min(candidates, key=lambda p: (p.price_decimal, p.book_key))

            if anchor is Anchor.REFERENCE:
                include = reference_books
            else:
                # Todo el feed menos el operador de la casa que se está evaluando.
                include = {p.book_key for p in prices if p.operator != best.operator} & known
            consensus = _consensus(prices, pair, include, method)
            if consensus is None:
                no_consensus += 1
                continue
            fair, n_operators = consensus

            edges.append(
                SelectionEdge(
                    event_id=event_id,
                    home_team=home,
                    away_team=away,
                    side=side,
                    anchor=anchor,
                    fair_probability=fair[side],
                    best_price_decimal=best.price_decimal,
                    best_book=best.book_key,
                    worst_price_decimal=worst.price_decimal,
                    consensus_operators=n_operators,
                )
            )

    return StructuralReport(
        anchor=anchor,
        edges=edges,
        n_events=len(payload),
        n_selections=n_selections,
        n_no_consensus=no_consensus,
        n_no_price=no_price,
        n_in_play=in_play,
        n_stale_prices=stale,
        observed_at=observed_at,
        unknown_books=unknown,
    )


def catalog_sets() -> tuple[set[str], set[str], dict[str, str]]:
    """Referencia, ejecutables y operadores, tal como los define el catálogo.

    `lowvig` entra en el universo de referencia aunque no esté marcada como tal:
    comparte operador con `betonlineag`, así que tiene que poder verse para que la
    deduplicación la descarte. Dejarla fuera sin más sería lo mismo en el
    resultado, pero por accidente en vez de por diseño.
    """
    from .seeds.catalog import SPORTSBOOKS

    reference = {key for key, _, _, is_ref, _, _ in SPORTSBOOKS if is_ref}
    reference |= {
        key
        for key, _, _, _, _, operator in SPORTSBOOKS
        if operator in {op for k, _, _, r, _, op in SPORTSBOOKS if r}
    }
    executable = {key for key, _, _, _, is_exec, _ in SPORTSBOOKS if is_exec}
    operators = {key: operator for key, _, _, _, _, operator in SPORTSBOOKS}
    return reference, executable, operators


def run(sport: str = "mlb") -> int:
    """Comando `sportstar structural`: mide el edge estructural del slate actual."""
    import os

    from .data.providers.the_odds_api import TheOddsApiProvider

    key = os.environ.get("SPORTSTAR_ODDS_API_KEY", "")
    if not key:
        print(
            "falta SPORTSTAR_ODDS_API_KEY. Sin ella no hay precios por casa, y sin\n"
            "precios por casa el edge estructural no se puede medir."
        )
        return 1

    reference, executable, operators = catalog_sets()
    fetch = TheOddsApiProvider(key).fetch_odds(sport)
    print(f"cuota restante: {fetch.quota_remaining}   eventos: {len(fetch.payload)}")
    print()

    for anchor in (Anchor.REFERENCE, Anchor.LEAVE_ONE_OUT):
        report = analyze(
            fetch.payload,
            reference_books=reference,
            executable_books=executable,
            operators=operators,
            anchor=anchor,
            observed_at=fetch.observed_at,
        )
        print(report.summary())
        print()
    return 0
