"""Lanzadores: quién abre cada partido y qué había hecho hasta entonces.

Dos payloads distintos, dos formas distintas:

- el **calendario hidratado** con `probablePitcher` dice quién abre cada partido
- el **game log** de un lanzador dice qué hizo en cada aparición, con fecha

La fecha de cada aparición es lo que permite reconstruir el estado de un lanzador
**antes** de una apertura concreta, que es lo único utilizable en un backtest.

Advertencia honesta sobre `probablePitcher` en histórico
--------------------------------------------------------
Para partidos ya jugados, la MLB Stats API devuelve en `probablePitcher` al
lanzador que **efectivamente abrió**, no necesariamente al que se anunció días
antes. Un abridor que se cae a última hora aparece aquí como si nunca hubiese
estado previsto.

Eso es una fuga pequeña pero real, y no se puede cerrar con esta fuente. Se
mitiga sola en parte: en el mercado real, una apuesta al moneyline de MLB con
"listed pitchers" se anula si cambia el abridor, así que el caso en el que la
fuga importaría es también el caso en el que la apuesta no existiría. Queda
registrado aquí porque una limitación que no está escrita se convierte en un
supuesto invisible.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from .errors import ShapeError, require_dict, require_list

PROVIDER = "mlb-stats-api"

#: Tipos de partido que cuentan. Mismo criterio que `features/mlb/history.py`:
#: la pretemporada se lanza con prospectos y no mide nada.
COMPETITIVE_GAME_TYPES = frozenset({"R", "F", "D", "L", "W", "P"})


@dataclass(frozen=True, slots=True)
class ProbableStarters:
    """Quién estaba previsto que abriese un partido, por lado."""

    official_date: date
    game_pk: int
    game_number: int
    home_team_id: int
    away_team_id: int
    home_pitcher_id: int | None
    away_pitcher_id: int | None
    #: El marcador es lo que permite desambiguar una doble jornada al cruzar con
    #: el archivo de odds, que no trae número de partido. Sin él, los dos
    #: partidos del día entre los mismos equipos son indistinguibles.
    home_score: int | None
    away_score: int | None

    @property
    def complete(self) -> bool:
        return self.home_pitcher_id is not None and self.away_pitcher_id is not None


@dataclass(frozen=True, slots=True)
class PitchingAppearance:
    """Una aparición de un lanzador, con la fecha en que ocurrió.

    `outs` en vez de entradas: la API devuelve entradas como `"6.1"`, que **no
    es un decimal** —significa 6 entradas y 1 out, o sea 19 outs—. Tratar `6.1`
    como 6,1 introduce un error silencioso en cada ratio por entrada.
    """

    game_date: date
    pitcher_id: int
    is_start: bool
    outs: int
    earned_runs: int
    strikeouts: int
    walks: int
    hits: int
    home_runs: int
    batters_faced: int

    @property
    def innings(self) -> float:
        return self.outs / 3.0


def parse_innings_pitched(text: str) -> int:
    """Convierte `"6.1"` en 19 outs. `.1` es un out, `.2` son dos.

    Es la trampa clásica de los datos de béisbol: la notación parece decimal y no
    lo es. `6.1` no es 6,1 entradas, son 6 entradas y 1 out.
    """
    raw = text.strip()
    if not raw:
        raise ShapeError(f"entradas lanzadas vacías: {text!r}")
    whole, _, fraction = raw.partition(".")
    try:
        outs = int(whole) * 3
    except ValueError as exc:
        raise ShapeError(f"entradas lanzadas no numéricas: {text!r}") from exc
    if fraction:
        if fraction not in ("0", "1", "2"):
            raise ShapeError(
                f"fracción de entrada inválida en {text!r}: solo .0, .1 y .2 existen "
                "(la notación de béisbol cuenta outs, no décimas)"
            )
        outs += int(fraction)
    return outs


def _int(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key, 0)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return int(value)


@dataclass(frozen=True, slots=True)
class _Side:
    team_id: int
    pitcher_id: int | None
    score: int | None


def _side(teams: dict[str, Any], name: str, *, path: str) -> _Side:
    """Equipo, abridor previsto y marcador de un lado del partido."""
    entry = require_dict(teams.get(name, {}), path=f"{path}.teams.{name}")
    team = require_dict(entry.get("team", {}), path=f"{path}.teams.{name}.team")
    pitcher = entry.get("probablePitcher")
    pitcher_id = (
        pitcher.get("id")
        if isinstance(pitcher, dict) and isinstance(pitcher.get("id"), int)
        else None
    )
    score = entry.get("score")
    return _Side(
        team_id=int(team.get("id", 0)),
        pitcher_id=pitcher_id,
        score=score if isinstance(score, int) and not isinstance(score, bool) else None,
    )


def normalize_probable_starters(payload: Any) -> list[ProbableStarters]:
    """Extrae los abridores previstos de un calendario hidratado."""
    root = require_dict(payload, path="payload")
    dates = require_list(root.get("dates", []), path="payload.dates")

    out: list[ProbableStarters] = []
    for day_index, day in enumerate(dates):
        day_dict = require_dict(day, path=f"payload.dates[{day_index}]")
        games = require_list(day_dict.get("games", []), path=f"payload.dates[{day_index}].games")
        for game_index, game in enumerate(games):
            path = f"payload.dates[{day_index}].games[{game_index}]"
            game_dict = require_dict(game, path=path)
            if game_dict.get("gameType") not in COMPETITIVE_GAME_TYPES:
                continue
            official = game_dict.get("officialDate")
            if not isinstance(official, str):
                continue
            teams = require_dict(game_dict.get("teams", {}), path=f"{path}.teams")
            home = _side(teams, "home", path=path)
            away = _side(teams, "away", path=path)
            out.append(
                ProbableStarters(
                    official_date=date.fromisoformat(official),
                    game_pk=_int(game_dict, "gamePk"),
                    game_number=_int(game_dict, "gameNumber") or 1,
                    home_team_id=home.team_id,
                    away_team_id=away.team_id,
                    home_pitcher_id=home.pitcher_id,
                    away_pitcher_id=away.pitcher_id,
                    home_score=home.score,
                    away_score=away.score,
                )
            )
    return out


def normalize_game_log(payload: Any, pitcher_id: int) -> list[PitchingAppearance]:
    """Extrae las apariciones de un game log de pitcheo.

    Un lanzador sin apariciones devuelve una lista vacía, no un error: es lo que
    pasa con un jugador que cambió de liga o se lesionó en marzo.
    """
    root = require_dict(payload, path="payload")
    groups = require_list(root.get("stats", []), path="payload.stats")
    if not groups:
        return []

    splits = require_list(
        require_dict(groups[0], path="payload.stats[0]").get("splits", []),
        path="payload.stats[0].splits",
    )

    out: list[PitchingAppearance] = []
    for index, split in enumerate(splits):
        path = f"payload.stats[0].splits[{index}]"
        entry = require_dict(split, path=path)
        if entry.get("gameType") not in COMPETITIVE_GAME_TYPES:
            continue
        raw_date = entry.get("date")
        if not isinstance(raw_date, str):
            continue
        stat = require_dict(entry.get("stat", {}), path=f"{path}.stat")
        innings = stat.get("inningsPitched")
        if not isinstance(innings, str):
            continue

        out.append(
            PitchingAppearance(
                game_date=date.fromisoformat(raw_date),
                pitcher_id=pitcher_id,
                is_start=_int(stat, "gamesStarted") == 1,
                outs=parse_innings_pitched(innings),
                earned_runs=_int(stat, "earnedRuns"),
                strikeouts=_int(stat, "strikeOuts"),
                walks=_int(stat, "baseOnBalls"),
                hits=_int(stat, "hits"),
                home_runs=_int(stat, "homeRuns"),
                batters_faced=_int(stat, "battersFaced"),
            )
        )
    return out
