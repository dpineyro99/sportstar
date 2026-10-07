"""Replay point-in-time: genera los candidates día a día, sin mirar adelante.

El bucle es la pieza donde el leakage se cuela o no se cuela, así que está
escrito para que el orden sea imposible de invertir por accidente:

    para cada día D, en orden:
        1. predecir TODOS los partidos de D          <- el modelo aún no vio D
        2. solo entonces, incorporar los resultados de D

Procesar por día y no por partido es lo que hace cumplir la convención de
`dataset.py`: dentro de un mismo día ningún resultado alimenta ninguna
predicción, ni siquiera el del partido de la tarde sobre el de la noche.

Cada candidate registra el `as_of` con el que se generó y el `observed_at` de los
datos que lo alimentaron, y esos pares se le pasan luego a `sanity.py`, que
comprueba de forma independiente que ninguno viola el contrato. El backtest no se
cree a sí mismo.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime

from ..core.clv import clv_price, clv_probability, model_beat_close
from ..core.edge import expected_value
from ..core.kelly import Stake, StakeConfig, recommend_stake
from ..core.novig import NoVigMethod, remove_vig
from ..filters.gates import FilterResult, GateInput, evaluate_gates
from .dataset import HistoricalGame
from .strategies import Strategy

SIDES = ("home", "away")

# --- Supuestos de los gates que el archivo histórico no puede evaluar ----------
#
# Los gates de `filters/gates.py` están escritos para el pipeline en vivo, donde
# hay varias casas, marcas de tiempo por precio y un precio ejecutable. El
# archivo de SBR no tiene nada de eso: es una línea de consenso sin hora y sin
# casa.
#
# Se les da el valor que los deja pasar, y se **registra cuáles son** para que el
# informe lo diga. La alternativa —inventar una dispersión entre casas o una
# antigüedad de línea plausibles— produciría un backtest que mide un filtro que
# nunca existió.
UNEVALUABLE_GATES = ("line_freshness", "reference_books", "data_quality", "model_agreement")
ASSUMED_LINE_AGE_SECONDS = 0.0
ASSUMED_REFERENCE_BOOKS = 2
ASSUMED_DATA_QUALITY = 1.0


@dataclass(frozen=True, slots=True)
class Candidate:
    """Una oportunidad evaluada. Puede o no haber pasado el filtro.

    La distinción entre candidate y recomendación es la que sostiene toda la
    medición: los candidates son la muestra grande con la que se juzga el
    **modelo**, las recomendaciones la muestra pequeña con la que se juzga el
    **filtro**. Mezclarlas es comparar cosas que difieren en dos órdenes de
    magnitud de muestra.
    """

    game_date: date
    season: int
    #: Distingue las dos mitades de una doble jornada. Ver `dataset.py`.
    archive_sequence: int
    side: str
    home_team: str
    away_team: str
    strategy: str
    strategy_version: str

    model_prob: float
    market_fair_prob: float
    taken_decimal: float
    closing_decimal: float
    closing_fair_prob: float

    edge: float
    structural_edge: float
    total_edge: float
    expected_value: float

    filter_result: FilterResult
    stake: Stake
    won: bool

    #: `as_of` con el que se generó, y `observed_at` del dato más reciente usado.
    as_of: datetime
    latest_input_observed_at: datetime | None

    @property
    def is_recommended(self) -> bool:
        return self.filter_result.is_recommended and self.stake.units > 0.0

    @property
    def profit_units(self) -> float:
        """Beneficio en units. Solo tiene sentido si se apostó."""
        if self.stake.units <= 0.0:
            return 0.0
        return self.stake.units * (self.taken_decimal - 1.0) if self.won else -self.stake.units

    @property
    def clv(self) -> float:
        """CLV de precio: cuánto mejor fue el precio tomado que el de cierre."""
        return clv_price(self.taken_decimal, self.closing_decimal)

    @property
    def beat_close(self) -> bool:
        return self.taken_decimal > self.closing_decimal

    @property
    def model_clv(self) -> float:
        """Cuánto se movió el mercado hacia nuestro lado, en probabilidad justa.

        Signo, no acierto: positivo significa que el cierre acabó por encima de
        lo que decía el modelo. Sobre una muestra grande y un modelo calibrado
        tiende a 0, así que **no sirve por sí solo para acreditar señal** —para
        eso está `model_beat_market`—.
        """
        return clv_probability(self.model_prob, self.closing_fair_prob)

    @property
    def model_beat_market(self) -> bool:
        """¿Estaba el modelo más cerca del cierre que el mercado de apertura?

        Es **la** métrica de `ARCHITECTURE.md` §4.6, y se mide sobre todos los
        candidates, apostados o no: por eso su muestra es uno o dos órdenes de
        magnitud mayor que la de cualquier ROI.

        Ojo con confundirla con "el cierre se movió hacia mi lado": eso es
        `model_clv`, y para un modelo calibrado sale ~50% por pura simetría,
        diga lo que diga el modelo. La confusión produce un número que parece
        señal y no mide nada.
        """
        return model_beat_close(self.model_prob, self.market_fair_prob, self.closing_fair_prob)


@dataclass(frozen=True, slots=True)
class ReplayResult:
    candidates: list[Candidate]
    games_replayed: int
    games_skipped_no_prediction: int
    unevaluable_gates: tuple[str, ...] = UNEVALUABLE_GATES


def _fair_probs(game: HistoricalGame, method: NoVigMethod) -> tuple[float, float] | None:
    """Probabilidades justas de apertura, o `None` si el mercado no es coherente."""
    implied = [game.home.open_implied, game.away.open_implied]
    if sum(implied) <= 1.0:
        return None
    fair = remove_vig(implied, method=method)
    return fair[0], fair[1]


def _closing_fair_home(game: HistoricalGame, method: NoVigMethod) -> float | None:
    implied = [game.home.close_implied, game.away.close_implied]
    if sum(implied) <= 1.0:
        return None
    return remove_vig(implied, method=method)[0]


def replay(
    games: list[HistoricalGame],
    strategy: Strategy,
    *,
    method: NoVigMethod = NoVigMethod.PROPORTIONAL,
    stake_config: StakeConfig | None = None,
) -> ReplayResult:
    """Recorre el histórico en orden y genera un candidate por lado y partido.

    El orden de las dos fases dentro de cada día no es un detalle de
    implementación: invertirlas produce un modelo que predice partidos que ya ha
    visto, y el síntoma sería un backtest excelente.
    """
    config = stake_config or StakeConfig()
    by_day: dict[date, list[HistoricalGame]] = defaultdict(list)
    for game in games:
        by_day[game.game_date.date()].append(game)

    candidates: list[Candidate] = []
    replayed = 0
    skipped = 0
    latest_observed: datetime | None = None

    for day in sorted(by_day):
        # --- Fase 1: predecir. El modelo todavía no ha visto nada de hoy. -----
        for game in by_day[day]:
            model_home = strategy.predict_home(game)
            fair = _fair_probs(game, method)
            closing_home = _closing_fair_home(game, method)
            if model_home is None or fair is None or closing_home is None:
                skipped += 1
                continue
            replayed += 1

            for side in SIDES:
                model_prob = model_home if side == "home" else 1.0 - model_home
                fair_prob = fair[0] if side == "home" else fair[1]
                closing_fair = closing_home if side == "home" else 1.0 - closing_home
                prices = game.prices(side)
                taken = prices.open_decimal

                model_edge = model_prob - fair_prob
                structural = fair_prob - 1.0 / taken
                total = model_edge + structural
                ev = expected_value(model_prob, taken)

                gate_result = evaluate_gates(
                    GateInput(
                        total_edge=total,
                        expected_value=ev,
                        line_age_seconds=ASSUMED_LINE_AGE_SECONDS,
                        reference_book_count=ASSUMED_REFERENCE_BOOKS,
                        data_quality=ASSUMED_DATA_QUALITY,
                        dispersion=None,
                        has_executable_price=True,
                    )
                )
                stake = (
                    recommend_stake(model_prob, taken, config)
                    if gate_result.is_recommended
                    else Stake(0.0, config.method, 0.0, 0.0, was_capped=False)
                )

                candidates.append(
                    Candidate(
                        game_date=day,
                        season=game.season,
                        archive_sequence=game.archive_sequence,
                        side=side,
                        home_team=game.home_team,
                        away_team=game.away_team,
                        strategy=strategy.name,
                        strategy_version=strategy.version,
                        model_prob=model_prob,
                        market_fair_prob=fair_prob,
                        taken_decimal=taken,
                        closing_decimal=prices.close_decimal,
                        closing_fair_prob=closing_fair,
                        edge=model_edge,
                        structural_edge=structural,
                        total_edge=total,
                        expected_value=ev,
                        filter_result=gate_result,
                        stake=stake,
                        won=game.won(side),
                        as_of=game.decided_at,
                        latest_input_observed_at=latest_observed,
                    )
                )

        # --- Fase 2: y solo ahora, aprender de lo que pasó hoy. ---------------
        for game in by_day[day]:
            strategy.observe(game)
            observed = game.observed_at
            if latest_observed is None or observed > latest_observed:
                latest_observed = observed

    return ReplayResult(
        candidates=candidates,
        games_replayed=replayed,
        games_skipped_no_prediction=skipped,
    )
