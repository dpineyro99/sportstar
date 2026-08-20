"""Elo point-in-time.

Baseline honesto y barato: una sola cifra por equipo que se actualiza partido a
partido. No pretende batir al mercado — su función es dar una referencia
comparable contra la que medir cualquier modelo posterior.

Es sport-agnostic: la K, la ventaja local y la regresión entre temporadas se
pasan como parámetros. MLB usa una K baja porque el resultado de un partido es
mayoritariamente ruido; el baloncesto tolera una mucho más alta.

**Cómo se garantiza el point-in-time.** El estado se construye recorriendo los
partidos en orden de `observed_at` y deteniéndose antes del corte. No hay un
"rating actual" global que consultar: cada `as_of` produce su propio estado. Es
más lento y es lo único que no miente.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

# K baja: en béisbol un partido suelto dice poco, y una K alta hace que el rating
# persiga el ruido. El valor se revisa contra datos en Phase 3.
DEFAULT_K = 4.0
# Ventaja local en puntos de rating. ~24 equivale a ~54% de victoria local entre
# iguales, que es el orden de magnitud histórico en MLB.
DEFAULT_HOME_ADVANTAGE = 24.0
DEFAULT_INITIAL_RATING = 1500.0
# Regresión a la media entre temporadas: los equipos cambian de plantilla y
# arrastrar el rating entero sobrestima la continuidad.
DEFAULT_SEASON_REGRESSION = 0.30


@dataclass(frozen=True, slots=True)
class GameResult:
    """Resultado ya conocido de un partido.

    `observed_at` es cuándo lo supimos, no cuándo se jugó. Un partido de anoche
    cuyo resultado llegó esta mañana no estaba disponible anoche.
    """

    season: int
    home_team_id: int
    away_team_id: int
    home_score: int
    away_score: int
    observed_at: datetime

    @property
    def home_won(self) -> bool:
        return self.home_score > self.away_score

    @property
    def is_tie(self) -> bool:
        return self.home_score == self.away_score


def win_probability(rating_diff: float) -> float:
    """Probabilidad logística estándar de Elo a partir de la diferencia."""
    return float(1.0 / (1.0 + 10.0 ** (-rating_diff / 400.0)))


@dataclass
class EloModel:
    """Ratings Elo reconstruibles a cualquier instante."""

    k: float = DEFAULT_K
    home_advantage: float = DEFAULT_HOME_ADVANTAGE
    initial_rating: float = DEFAULT_INITIAL_RATING
    season_regression: float = DEFAULT_SEASON_REGRESSION
    ratings: dict[int, float] = field(default_factory=dict)
    games_seen: dict[int, int] = field(default_factory=dict)
    _season: int | None = None

    def rating(self, team_id: int) -> float:
        return self.ratings.get(team_id, self.initial_rating)

    def sample_size(self, team_id: int) -> int:
        """Partidos ya incorporados. Alimenta el componente de muestra del confidence."""
        return self.games_seen.get(team_id, 0)

    def expected_home_win(self, home_team_id: int, away_team_id: int) -> float:
        diff = self.rating(home_team_id) + self.home_advantage - self.rating(away_team_id)
        return win_probability(diff)

    def _apply_season_change(self, season: int) -> None:
        """Regresa los ratings hacia la media al cambiar de temporada."""
        if self._season is not None and season != self._season:
            for team_id, value in self.ratings.items():
                self.ratings[team_id] = value + self.season_regression * (
                    self.initial_rating - value
                )
        self._season = season

    def update(self, game: GameResult) -> None:
        """Incorpora un resultado. El orden importa: se llama cronológicamente."""
        self._apply_season_change(game.season)

        expected = self.expected_home_win(game.home_team_id, game.away_team_id)
        actual = 0.5 if game.is_tie else (1.0 if game.home_won else 0.0)
        delta = self.k * (actual - expected)

        self.ratings[game.home_team_id] = self.rating(game.home_team_id) + delta
        self.ratings[game.away_team_id] = self.rating(game.away_team_id) - delta
        for team_id in (game.home_team_id, game.away_team_id):
            self.games_seen[team_id] = self.games_seen.get(team_id, 0) + 1


def fit_through(
    games: list[GameResult],
    as_of: datetime,
    **params: float,
) -> EloModel:
    """Ratings usando **solo** los partidos conocidos antes de `as_of`.

    Reconstruir el estado desde cero en cada corte es deliberado. La alternativa
    —mantener un rating "actual" y consultarlo— es más rápida y es exactamente
    cómo se cuela el leakage: basta con que un partido se haya incorporado antes
    de tiempo para que el backtest deje de significar nada, y no hay forma de
    notarlo mirando el resultado.

    Los partidos se ordenan por `observed_at`, no por fecha de juego: el orden en
    que supimos las cosas es el que determina qué sabíamos.
    """
    model = EloModel(**params)  # type: ignore[arg-type]
    for game in sorted(games, key=lambda g: g.observed_at):
        if game.observed_at >= as_of:
            break
        model.update(game)
    return model
