"""Mercado + correcciones: la forma honesta de preguntar si una feature aporta.

El planteamiento
----------------
Phase 3 dejó claro que Elo solo es peor que el mercado. La pregunta que queda no
es "¿es mi modelo bueno?" sino "**¿contiene mi feature algo que el mercado no
tenga ya?**", y esa se responde con una regresión logística sobre::

    logit(P) = b0 + b1·logit(mercado) + b2·elo_diff + b3·ventaja_abridor

Si una corrección no aporta nada, su coeficiente sale ~0 y el modelo colapsa al
mercado. No hace falta creerse nada: el propio ajuste lo dice, y el holdout lo
confirma o lo desmiente.

Es mejor formulación que "Elo contra mercado" porque no obliga a elegir. Un
modelo que solo puede sustituir al mercado tiene que ser mejor que él en todo;
uno que lo corrige solo tiene que aportar algo en el margen, que es una barrera
mucho más baja — y aun así hay que superarla.

El contrato point-in-time
-------------------------
Las features se calculan con el mismo `FeatureState` en las dos fases —al
construir las filas de entrenamiento y al predecir en el replay— para que sea
imposible que difieran. El estado se consulta antes de incorporar el día, igual
que en `replay.py`; el orden lo controla el motor, no la estrategia.

Los coeficientes se ajustan **una vez sobre train** y se congelan. Reajustarlos
sobre el holdout convertiría el holdout en train, que es justo lo que el ledger
de `splits.py` existe para hacer visible.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ..core.novig import NoVigMethod, remove_vig
from ..data.normalizers.mlb_pitchers import PitchingAppearance
from ..features.elo import EloModel
from ..features.mlb.pitchers import PitcherForm
from .dataset import HistoricalGame

#: Nombres de las features, en el orden del vector. Se persisten con el modelo:
#: un vector de coeficientes sin los nombres es imposible de auditar.
FEATURE_NAMES = ("market_logit", "elo_diff", "starter_advantage")

#: Partidos mínimos por equipo antes de fiarse del rating Elo.
DEFAULT_MIN_GAMES = 20

# Recorte del logit del mercado. Una probabilidad de 0 o 1 daría un logit
# infinito; ninguna línea real llega ahí, pero un dato corrupto sí podría.
_EPS = 1e-6


def _logit(p: float) -> float:
    clipped = min(1.0 - _EPS, max(_EPS, p))
    return math.log(clipped / (1.0 - clipped))


def _sigmoid(z: float) -> float:
    return 1.0 / (1.0 + math.exp(-z))


@dataclass
class FeatureState:
    """El estado del mundo, avanzando en el tiempo. Una sola implementación.

    La usan tanto el constructor de filas de entrenamiento como la estrategia que
    predice. Duplicar el cálculo de features en dos sitios es la forma más común
    de que entrenamiento y producción diverjan sin que nadie lo note.
    """

    method: NoVigMethod = NoVigMethod.PROPORTIONAL
    min_games: int = DEFAULT_MIN_GAMES
    elo: EloModel = field(default_factory=EloModel)
    form: PitcherForm = field(default_factory=PitcherForm)
    appearances_by_date: dict[date, list[PitchingAppearance]] = field(default_factory=dict)
    _consumed: set[date] = field(default_factory=set)

    def market_home(self, game: HistoricalGame) -> float | None:
        implied = [game.home.open_implied, game.away.open_implied]
        if sum(implied) <= 1.0:
            return None
        return remove_vig(implied, method=self.method)[0]

    def vector(self, game: HistoricalGame) -> list[float] | None:
        """Features del partido, o `None` si falta alguna.

        Devolver `None` en vez de rellenar con ceros es deliberado: un cero en
        `starter_advantage` significa "los dos abridores son igual de buenos", y
        eso no es lo mismo que "no sé quién lanza". Quien llame decide qué hacer
        con la ausencia; aquí no se inventa.
        """
        market = self.market_home(game)
        if market is None:
            return None
        played = min(
            self.elo.sample_size(game.home_team_id), self.elo.sample_size(game.away_team_id)
        )
        if played < self.min_games:
            return None
        advantage = self.form.advantage(game.home_pitcher_id, game.away_pitcher_id)
        if advantage is None:
            return None
        elo_diff = (
            self.elo.rating(game.home_team_id)
            + self.elo.home_advantage
            - self.elo.rating(game.away_team_id)
        )
        return [_logit(market), elo_diff, advantage]

    def observe(self, game: HistoricalGame) -> None:
        """Incorpora el resultado del partido y las apariciones de su día."""
        self.elo.update(game.result)
        day = game.game_date.date()
        if day not in self._consumed:
            self._consumed.add(day)
            self.form.observe_all(self.appearances_by_date.get(day, []))


def index_appearances(
    appearances: list[PitchingAppearance],
) -> dict[date, list[PitchingAppearance]]:
    by_date: dict[date, list[PitchingAppearance]] = defaultdict(list)
    for appearance in appearances:
        by_date[appearance.game_date].append(appearance)
    return dict(by_date)


@dataclass(frozen=True, slots=True)
class TrainingRows:
    features: list[list[float]]
    labels: list[int]
    n_skipped: int

    def __len__(self) -> int:
        return len(self.labels)


def build_rows(
    games: list[HistoricalGame],
    appearances: list[PitchingAppearance],
    *,
    method: NoVigMethod = NoVigMethod.PROPORTIONAL,
    min_games: int = DEFAULT_MIN_GAMES,
) -> TrainingRows:
    """Recorre el histórico en orden y produce las filas de entrenamiento.

    Mismo bucle en dos fases que `replay.py`: predecir todo el día, y solo
    entonces incorporarlo. Una fila de entrenamiento generada con información del
    propio día enseñaría al modelo a leer el futuro, y el síntoma sería un ajuste
    excelente que no se reproduce en holdout.
    """
    state = FeatureState(
        method=method, min_games=min_games, appearances_by_date=index_appearances(appearances)
    )
    by_day: dict[date, list[HistoricalGame]] = defaultdict(list)
    for game in games:
        by_day[game.game_date.date()].append(game)

    features: list[list[float]] = []
    labels: list[int] = []
    skipped = 0

    for day in sorted(by_day):
        for game in by_day[day]:
            vector = state.vector(game)
            if vector is None:
                skipped += 1
                continue
            features.append(vector)
            labels.append(int(game.home_won))
        for game in by_day[day]:
            state.observe(game)

    return TrainingRows(features=features, labels=labels, n_skipped=skipped)


@dataclass(frozen=True, slots=True)
class Coefficients:
    """Los coeficientes ajustados, con sus nombres. Auditables a simple vista."""

    intercept: float
    weights: tuple[float, ...]
    names: tuple[str, ...] = FEATURE_NAMES
    n_train: int = 0

    def as_dict(self) -> dict[str, float]:
        return dict(zip(self.names, self.weights, strict=True))

    def explain(self) -> str:
        parts = [f"intercepto {self.intercept:+.4f}"]
        parts += [f"{name} {weight:+.4f}" for name, weight in self.as_dict().items()]
        return f"n={self.n_train}   " + "   ".join(parts)

    def predict(self, vector: list[float]) -> float:
        """`vector` viene siempre completo, en el orden de `FEATURE_NAMES`.

        Se seleccionan aquí las columnas que este ajuste usa, para que quien
        construye el vector no tenga que saber con qué subconjunto se entrenó.
        """
        columns = [FEATURE_NAMES.index(name) for name in self.names]
        total = self.intercept + sum(
            w * vector[i] for w, i in zip(self.weights, columns, strict=True)
        )
        return _sigmoid(total)


def fit(
    rows: TrainingRows, *, C: float = 1.0, use: tuple[str, ...] = FEATURE_NAMES
) -> Coefficients:
    """Ajusta la logística. Sin estandarizar: los coeficientes se leen en crudo.

    Estandarizar mejoraría el condicionamiento pero haría los coeficientes
    ilegibles, y aquí su legibilidad **es** el resultado: lo que se quiere saber
    es si `starter_advantage` pesa algo, no cuánto pesa una versión escalada de
    ella. La regularización L2 se queda porque el coste de un coeficiente inflado
    en este dominio es un stake inflado.

    `use` permite ajustar sobre un subconjunto de features, y sirve para el
    diagnóstico que de verdad importa. Un coeficiente ~0 sobre
    `starter_advantage` **con** el mercado dentro admite dos lecturas muy
    distintas: que la feature no vale nada, o que el mercado ya la contiene. Se
    distinguen ajustando sin el mercado: si ahí sí predice, la feature es buena y
    el mercado se le adelantó.
    """
    from sklearn.linear_model import LogisticRegression

    if not rows.labels:
        raise ValueError("no se puede ajustar sin filas")

    unknown = set(use) - set(FEATURE_NAMES)
    if unknown:
        raise ValueError(f"features desconocidas: {sorted(unknown)}")

    columns = [FEATURE_NAMES.index(name) for name in use]
    features = [[row[i] for i in columns] for row in rows.features]

    model: Any = LogisticRegression(C=C, max_iter=1000)
    model.fit(features, rows.labels)
    return Coefficients(
        intercept=float(model.intercept_[0]),
        weights=tuple(float(w) for w in model.coef_[0]),
        names=use,
        n_train=len(rows),
    )


class MarketPlusCorrections:
    """Estrategia con coeficientes ya ajustados. No aprende durante el replay.

    Cuando le faltan features cae al mercado, que es la decisión conservadora: sin
    información adicional, la mejor estimación disponible sigue siendo el precio.
    """

    name = "market_plus"

    def __init__(
        self,
        coefficients: Coefficients,
        appearances: list[PitchingAppearance],
        *,
        method: NoVigMethod = NoVigMethod.PROPORTIONAL,
        min_games: int = DEFAULT_MIN_GAMES,
        version: str = "v1",
    ) -> None:
        self._coefficients = coefficients
        self._state = FeatureState(
            method=method,
            min_games=min_games,
            appearances_by_date=index_appearances(appearances),
        )
        self.version = version

    @property
    def coefficients(self) -> Coefficients:
        return self._coefficients

    def predict_home(self, game: HistoricalGame) -> float | None:
        vector = self._state.vector(game)
        if vector is None:
            return self._state.market_home(game)
        return self._coefficients.predict(vector)

    def observe(self, game: HistoricalGame) -> None:
        self._state.observe(game)
