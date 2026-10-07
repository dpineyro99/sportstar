"""Features de MLB, construidas en una sola pasada cronológica.

Todas salen del calendario: Elo, forma reciente, descanso, récord de temporada y
splits local/visitante. Ninguna necesita datos que no tengamos.

**Lo que NO está aquí, y por qué.** El calendario trae el *nombre* del pitcher
probable, pero no su calidad. Una feature de "pitcher" construida solo con el
nombre no aporta nada, y ponerla para que la lista parezca completa sería peor
que no tenerla: el modelo le asignaría un peso y nadie sabría que ese peso no
significa nada. El pitcher entra cuando entren sus estadísticas.

**Cómo se garantiza el point-in-time.** Una única pasada en orden de
`observed_at`: se leen los acumuladores, se emite la fila, y **solo entonces** se
incorpora el resultado. Invertir esas dos últimas operaciones daría un modelo que
predice partidos que ya vio, y el síntoma sería un backtest excelente.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime

from ..elo import DEFAULT_HOME_ADVANTAGE, EloModel, GameResult

FORM_WINDOW = 10
# Tope al descanso: en MLB casi todo es 0 o 1 día, y el parón del All-Star mete
# huecos de una semana que no significan "muy descansado", solo "hubo parón".
# Sin tope, esos valores extremos dominarían el coeficiente.
MAX_REST_DAYS = 5.0

# Todo lo que el builder sabe calcular. NO es el conjunto que usa el modelo.
FEATURE_NAMES: tuple[str, ...] = (
    "elo_diff",
    "form_diff",
    "rest_diff",
    "season_win_pct_diff",
    "venue_win_pct_diff",
)

# Lo que el modelo usa de verdad, y por qué es una sola columna.
#
# Medido sobre la temporada 2024: `elo_diff`, `season_win_pct_diff` y
# `venue_win_pct_diff` correlacionan entre 0.82 y 0.93 entre sí. No son tres
# señales, son una medida tres veces. Con las cinco, la regresión repartió el
# peso de forma arbitraria y **cuatro de cinco coeficientes salieron con el signo
# invertido**: mejor Elo predecía menos victorias.
#
# Las métricas apenas se movían (Brier 0.2425 con cinco features, 0.2426 con
# `elo_diff` sola), así que el problema no se veía en los números agregados. Lo
# que sí se rompía eran las explicaciones: este sistema deriva las razones de los
# coeficientes, y un coeficiente invertido convierte "descanso: -0.07%" en
# desinformación con formato de dato.
#
# Con una sola columna el coeficiente sale positivo, la calibración mejora
# (ECE 0.027 frente a 0.035) y la explicación dice la verdad.
#
# La elección es por parsimonia e interpretabilidad, **no** por rendimiento en
# test: elegir por test convierte el test en entrenamiento.
DEFAULT_MODEL_FEATURES: tuple[str, ...] = ("elo_diff",)


@dataclass
class _TeamState:
    """Acumuladores de un equipo. Solo contiene el pasado."""

    recent: deque[float] = field(default_factory=lambda: deque(maxlen=FORM_WINDOW))
    last_played: datetime | None = None
    wins: int = 0
    games: int = 0
    home_wins: int = 0
    home_games: int = 0
    away_wins: int = 0
    away_games: int = 0

    @property
    def form(self) -> float:
        """Victorias en la ventana reciente. 0.5 sin historial: ni bueno ni malo."""
        return sum(self.recent) / len(self.recent) if self.recent else 0.5

    @property
    def season_win_pct(self) -> float:
        return self.wins / self.games if self.games else 0.5

    def venue_win_pct(self, *, at_home: bool) -> float:
        wins, games = (
            (self.home_wins, self.home_games) if at_home else (self.away_wins, self.away_games)
        )
        return wins / games if games else 0.5

    def rest_days(self, now: datetime) -> float:
        """Días desde el último partido, acotados.

        Sin historial devuelve el tope: un equipo que debuta está descansado.
        """
        if self.last_played is None:
            return MAX_REST_DAYS
        return min((now - self.last_played).total_seconds() / 86400.0, MAX_REST_DAYS)


@dataclass(frozen=True, slots=True)
class MlbFeatureRow:
    """Una fila de entrenamiento: features previas y el resultado que siguió."""

    game: GameResult
    values: dict[str, float]
    home_games_played: int
    away_games_played: int

    @property
    def label(self) -> int:
        """1 si ganó el local. Los empates no existen en MLB."""
        return int(self.game.home_won)

    @property
    def min_games_played(self) -> int:
        return min(self.home_games_played, self.away_games_played)

    def vector(self, names: tuple[str, ...] = FEATURE_NAMES) -> list[float]:
        return [self.values[name] for name in names]


def build_season_features(
    games: list[GameResult],
    *,
    home_advantage: float = DEFAULT_HOME_ADVANTAGE,
    **elo_params: float,
) -> list[MlbFeatureRow]:
    """Features de cada partido usando solo lo anterior a él.

    Todas las features son **diferencias** local menos visitante en vez de dos
    columnas separadas. Es deliberado: al modelo le da igual que un equipo tenga
    .600 si el rival tiene .620, y la diferencia captura eso con la mitad de
    parámetros. Con 2.400 filas, gastar el doble de coeficientes en decir lo
    mismo es cómo se sobreajusta.
    """
    elo = EloModel(home_advantage=home_advantage, **elo_params)  # type: ignore[arg-type]
    state: dict[int, _TeamState] = defaultdict(_TeamState)
    rows: list[MlbFeatureRow] = []

    for game in sorted(games, key=lambda g: g.observed_at):
        home, away = state[game.home_team_id], state[game.away_team_id]
        moment = game.observed_at

        rows.append(
            MlbFeatureRow(
                game=game,
                values={
                    "elo_diff": elo.rating(game.home_team_id) - elo.rating(game.away_team_id),
                    "form_diff": home.form - away.form,
                    "rest_diff": home.rest_days(moment) - away.rest_days(moment),
                    "season_win_pct_diff": home.season_win_pct - away.season_win_pct,
                    "venue_win_pct_diff": (
                        home.venue_win_pct(at_home=True) - away.venue_win_pct(at_home=False)
                    ),
                },
                home_games_played=elo.sample_size(game.home_team_id),
                away_games_played=elo.sample_size(game.away_team_id),
            )
        )

        # A partir de aquí el resultado ya es pasado. Nada de lo de arriba lo vio.
        elo.update(game)
        home_won = game.home_won
        for team, won, at_home in ((home, home_won, True), (away, not home_won, False)):
            team.recent.append(float(won))
            team.last_played = moment
            team.games += 1
            team.wins += int(won)
            if at_home:
                team.home_games += 1
                team.home_wins += int(won)
            else:
                team.away_games += 1
                team.away_wins += int(won)

    return rows
