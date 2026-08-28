"""Cruce entre el archivo de odds y los abridores de la MLB Stats API.

Las dos fuentes no comparten identificador: el archivo de SBR trae apodos
("Yankees") y fechas, la MLB Stats API trae ids numéricos y `officialDate`. El
cruce se hace por **(fecha, equipos, marcador)**.

El marcador no está de adorno. 341 partidos del archivo son dobles jornadas: dos
partidos el mismo día entre los mismos equipos. Sin el marcador son
indistinguibles, y el cruce tendría que elegir al azar cuál de los dos abridores
va con cuál de los dos partidos — lo que ensuciaría el 2,7% de la muestra con
emparejamientos inventados. Con el marcador, cada partido tiene una tupla única
salvo en el caso rarísimo de dos partidos idénticos el mismo día, que se
descartan y se cuentan.

Todo lo que no cruza **se descarta y se informa**. Un cruce que rellena huecos
con supuestos convierte un dato ausente en un dato falso, y el modelo no puede
distinguirlos.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import date

from ..data.normalizers.mlb_pitchers import ProbableStarters
from .dataset import HistoricalGame

#: Apodos del archivo de SBR -> `clubName` de la MLB Stats API. Solo hace falta
#: para los que no coinciden literalmente.
CLUB_ALIASES: dict[str, str] = {
    # Cleveland pasó a llamarse Guardians en 2022; el archivo llega a 2021 y usa
    # el nombre de entonces. El id de equipo (114) no cambió con el renombre.
    "Indians": "Guardians",
}


@dataclass(frozen=True, slots=True)
class JoinResult:
    """Partidos con abridor conocido, y cuentas de lo que se quedó fuera."""

    matched: dict[int, tuple[int, int]]
    n_games: int
    n_matched: int
    n_no_starters: int
    n_ambiguous: int
    n_unmatched: int

    @property
    def match_rate(self) -> float:
        return self.n_matched / self.n_games if self.n_games else 0.0

    def summary(self) -> str:
        return (
            f"cruce de abridores: {self.n_matched}/{self.n_games} "
            f"({self.match_rate:.1%})   "
            f"sin abridor {self.n_no_starters}, ambiguos {self.n_ambiguous}, "
            f"sin pareja {self.n_unmatched}"
        )


def team_id_map(club_ids: dict[str, int]) -> dict[str, int]:
    """Apodo del archivo -> id de equipo de la MLB, aplicando alias."""
    return {
        nickname: club_ids[CLUB_ALIASES.get(nickname, nickname)]
        for nickname in {*club_ids, *CLUB_ALIASES}
        if CLUB_ALIASES.get(nickname, nickname) in club_ids
    }


def _key(
    day: date, home: int, away: int, home_score: int, away_score: int
) -> tuple[date, int, int, int, int]:
    return (day, home, away, home_score, away_score)


def join_starters(
    games: list[HistoricalGame],
    starters: list[ProbableStarters],
    nickname_to_id: dict[str, int],
) -> JoinResult:
    """Empareja cada partido del archivo con sus abridores.

    Devuelve un índice `posición del partido -> (abridor local, abridor
    visitante)`. Se indexa por posición y no por objeto porque `HistoricalGame`
    no es hashable de forma útil para esto y porque la posición es lo que el
    replay ya tiene a mano.
    """
    index: dict[tuple[date, int, int, int, int], list[ProbableStarters]] = defaultdict(list)
    for entry in starters:
        if entry.home_score is None or entry.away_score is None:
            continue
        index[
            _key(
                entry.official_date,
                entry.home_team_id,
                entry.away_team_id,
                entry.home_score,
                entry.away_score,
            )
        ].append(entry)

    matched: dict[int, tuple[int, int]] = {}
    no_starters = ambiguous = unmatched = 0

    for position, game in enumerate(games):
        home_id = nickname_to_id.get(game.home_team)
        away_id = nickname_to_id.get(game.away_team)
        if home_id is None or away_id is None:
            unmatched += 1
            continue

        candidates = index.get(
            _key(game.game_date.date(), home_id, away_id, game.home_score, game.away_score), []
        )
        if not candidates:
            unmatched += 1
            continue
        if len(candidates) > 1:
            # Dos partidos idénticos el mismo día entre los mismos equipos: no
            # hay forma de saber cuál es cuál, así que ninguno.
            ambiguous += 1
            continue

        entry = candidates[0]
        if entry.home_pitcher_id is None or entry.away_pitcher_id is None:
            no_starters += 1
            continue
        matched[position] = (entry.home_pitcher_id, entry.away_pitcher_id)

    return JoinResult(
        matched=matched,
        n_games=len(games),
        n_matched=len(matched),
        n_no_starters=no_starters,
        n_ambiguous=ambiguous,
        n_unmatched=unmatched,
    )


def enrich(games: list[HistoricalGame], result: JoinResult) -> list[HistoricalGame]:
    """Devuelve los partidos con los abridores puestos donde el cruce los encontró.

    Los que no cruzaron salen **sin abridor**, no con uno inventado. Que el
    modelo tenga que decidir qué hacer con un `None` es justo lo que se quiere:
    la alternativa es que reciba un valor neutro y no pueda distinguir "los dos
    abridores son igual de buenos" de "no tengo ni idea de quién lanza".
    """
    return [
        replace(
            game,
            home_pitcher_id=result.matched[position][0],
            away_pitcher_id=result.matched[position][1],
        )
        if position in result.matched
        else game
        for position, game in enumerate(games)
    ]
