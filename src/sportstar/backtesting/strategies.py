"""Estrategias: de un partido histórico a una probabilidad de modelo.

Una estrategia solo hace una cosa: dado el estado del mundo **antes** del
partido, decir qué probabilidad le da al local. Todo lo demás —edge, gates,
sizing, settlement— lo hace el motor, igual para todas. Así dos estrategias son
comparables por construcción, y una no puede colarse un atajo que la otra no
tenga.

El contrato point-in-time se cumple por la forma del protocolo, no por
disciplina: `observe()` recibe los resultados **después** de que `predict()` los
haya necesitado, y el motor es quien controla ese orden. Una estrategia no tiene
acceso al partido futuro aunque quiera.
"""

from __future__ import annotations

from typing import Protocol

from ..core.novig import NoVigMethod, remove_vig
from ..features.elo import EloModel
from .dataset import HistoricalGame


class Strategy(Protocol):
    """Cómo predice una estrategia. `version` acaba en cada predicción."""

    name: str
    version: str

    def predict_home(self, game: HistoricalGame) -> float | None:
        """Probabilidad de victoria local, o `None` si aún no puede opinar."""

    def observe(self, game: HistoricalGame) -> None:
        """Incorpora un resultado ya conocido."""


class MarketConsensus:
    """El suelo: la probabilidad justa de la línea de **apertura**.

    Es la baseline contra la que se mide todo lo demás. Su edge de modelo es 0
    por construcción —predice exactamente lo que dice el mercado—, así que no
    recomienda nada; su valor es dar el listón de calibración que cualquier
    modelo tiene que superar para merecer existir.

    Usa la apertura, no el cierre: el cierre es información que en el momento de
    apostar no existía, y usarlo aquí convertiría la baseline en un oráculo.
    """

    name = "market_consensus"
    version = "v1"

    def __init__(self, method: NoVigMethod = NoVigMethod.PROPORTIONAL) -> None:
        self._method = method

    def predict_home(self, game: HistoricalGame) -> float | None:
        total = game.home.open_implied + game.away.open_implied
        if total <= 1.0:
            return None
        return remove_vig([game.home.open_implied, game.away.open_implied], method=self._method)[0]

    def observe(self, game: HistoricalGame) -> None:
        """No aprende de resultados: su información es el precio."""


class Elo:
    """Elo puro, sin mirar el mercado.

    `min_games` evita opinar sobre equipos con dos partidos jugados: al principio
    de temporada el rating es casi el inicial, la predicción es ruido, y ese ruido
    entra al backtest como si fuese señal.
    """

    name = "elo"
    version = "v1"

    def __init__(self, *, min_games: int = 20, **params: float) -> None:
        self._model = EloModel(**params)  # type: ignore[arg-type]
        self._min_games = min_games

    def predict_home(self, game: HistoricalGame) -> float | None:
        played = min(
            self._model.sample_size(game.home_team_id),
            self._model.sample_size(game.away_team_id),
        )
        if played < self._min_games:
            return None
        return self._model.expected_home_win(game.home_team_id, game.away_team_id)

    def observe(self, game: HistoricalGame) -> None:
        self._model.update(game.result)


class EloBlend:
    """Elo mezclado con el mercado de apertura, con peso fijo.

    Existe porque es la forma honesta de preguntar *"¿aporta Elo algo que el
    mercado no tenga ya?"*. Si mezclar un 10% de Elo mejora la calibración sobre
    el mercado solo, Elo contiene información marginal; si la empeora, no la
    contiene, y el resultado se acepta.

    El peso es un parámetro, y como cualquier parámetro se elige en train y se
    mide en test. Barrerlo sobre el test set convertiría el test en train.
    """

    name = "elo_blend"

    def __init__(self, weight: float = 0.10, *, min_games: int = 20, **params: float) -> None:
        if not 0.0 <= weight <= 1.0:
            raise ValueError(f"weight debe estar en [0, 1], recibido {weight}")
        self._weight = weight
        self._elo = Elo(min_games=min_games, **params)
        self._market = MarketConsensus()
        self.version = f"v1-w{weight:.2f}"

    def predict_home(self, game: HistoricalGame) -> float | None:
        market = self._market.predict_home(game)
        if market is None:
            return None
        elo = self._elo.predict_home(game)
        if elo is None:
            return market
        return (1.0 - self._weight) * market + self._weight * elo

    def observe(self, game: HistoricalGame) -> None:
        self._elo.observe(game)
