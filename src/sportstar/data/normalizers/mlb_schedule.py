"""Normalizador del calendario de MLB Stats API.

Forma esperada (`/api/v1/schedule?sportId=1&date=...&hydrate=probablePitcher,...`):

    {"dates": [{"date": "2026-08-19", "games": [
        {"gamePk": 748534,
         "gameDate": "2026-08-19T23:05:00Z",
         "gameNumber": 1, "doubleHeader": "N",
         "status": {"abstractGameState": "Preview"},
         "teams": {"home": {"score": 0, "team": {"id": 147, "name": "New York Yankees"},
                            "probablePitcher": {"fullName": "..."}},
                   "away": {...}},
         "venue": {"name": "Yankee Stadium"}}]}]}

`gamePk` es un entero y es el identificador estable del partido. `gameNumber`
distingue los partidos de un doblete, que comparten fecha y equipos y son la
causa clásica de eventos duplicados.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from ..providers.mlb_stats_api import PROVIDER_KEY
from .errors import ShapeError, require_dict, require_key, require_list, require_str
from .models import NormalizationResult, NormalizedEvent
from .odds_api import parse_iso8601

# `abstractGameState` del proveedor -> `db.enums.EventStatus`.
STATUS_MAP: dict[str, str] = {
    "Preview": "scheduled",
    "Live": "live",
    "Final": "final",
    "Other": "postponed",
}

# `abstractGameState` NO basta. MLB marca los partidos aplazados y cancelados
# como "Final" —el partido "terminó" en el sentido de que ya no va a jugarse— y
# solo `detailedState` los distingue. Medido sobre la temporada 2024: 36
# aplazados y 6 cancelados de 2.574, todos con abstractGameState "Final".
#
# Guardarlos como terminados tiene tres consecuencias, ninguna cosmética:
#
# 1. Data Health los marcaría eternamente como partidos sin closing line.
# 2. La liquidación intentaría resolver apuestas de partidos que no se jugaron.
#    Un cancelado es VOID —se devuelve el dinero—, no una derrota.
# 3. Entrarían al histórico del modelo como partidos reales sin marcador.
DETAILED_STATUS_OVERRIDES: dict[str, str] = {
    "Postponed": "postponed",
    "Cancelled": "cancelled",
    "Canceled": "cancelled",  # el proveedor usa ambas grafías
    "Suspended": "postponed",
}


def normalize_schedule(payload: object) -> NormalizationResult:
    """Normaliza un calendario diario."""
    result = NormalizationResult()
    root = require_dict(payload, path="payload")
    dates = require_list(require_key(root, "dates", path="payload"), path="payload.dates")

    for d_index, raw_date in enumerate(dates):
        d_path = f"payload.dates[{d_index}]"
        date_entry = require_dict(raw_date, path=d_path)
        games = require_list(date_entry.get("games", []), path=f"{d_path}.games")

        for g_index, raw_game in enumerate(games):
            g_path = f"{d_path}.games[{g_index}]"
            try:
                result.events.append(_normalize_game(require_dict(raw_game, path=g_path), g_path))
            except ShapeError as exc:
                result.errors.append(str(exc))

    return result


def _team_side(teams: dict[str, Any], side: str, *, path: str) -> dict[str, Any]:
    return require_dict(require_key(teams, side, path=path), path=f"{path}.{side}")


def _normalize_game(game: dict[str, Any], path: str) -> NormalizedEvent:
    teams = require_dict(require_key(game, "teams", path=path), path=f"{path}.teams")
    home = _team_side(teams, "home", path=f"{path}.teams")
    away = _team_side(teams, "away", path=f"{path}.teams")

    home_path = f"{path}.teams.home"
    away_path = f"{path}.teams.away"
    home_team = require_dict(require_key(home, "team", path=home_path), path=f"{home_path}.team")
    away_team = require_dict(require_key(away, "team", path=away_path), path=f"{away_path}.team")

    status = require_dict(game.get("status", {}), path=f"{path}.status")
    resolved_status = _resolve_status(status)

    venue = require_dict(game.get("venue", {}), path=f"{path}.venue")

    return NormalizedEvent(
        provider=PROVIDER_KEY,
        provider_event_id=str(require_key(game, "gamePk", path=path)),
        sport_key="mlb",
        start_time=parse_iso8601(require_str(game, "gameDate", path=path), path=f"{path}.gameDate"),
        official_date=_official_date(game, path),
        home_team_raw=require_str(home_team, "name", path=f"{home_path}.team"),
        away_team_raw=require_str(away_team, "name", path=f"{away_path}.team"),
        status=resolved_status,
        game_type=game.get("gameType") if isinstance(game.get("gameType"), str) else None,
        # MLB manda `score: 0` también en partidos que no han empezado. Guardar
        # ese 0 haría un partido sin jugar indistinguible de un 0-0 terminado, y
        # la liquidación de apuestas depende justo de esa distinción. Solo se
        # conserva el marcador cuando el partido se jugó de verdad.
        home_score=_score_if_played(home, resolved_status),
        away_score=_score_if_played(away, resolved_status),
        venue_raw=venue.get("name") if isinstance(venue.get("name"), str) else None,
        home_probable_pitcher_raw=_pitcher_name(home),
        away_probable_pitcher_raw=_pitcher_name(away),
        provider_home_team_id=_optional_str(home_team.get("id")),
        provider_away_team_id=_optional_str(away_team.get("id")),
        # Los dobletes comparten fecha y equipos: sin `gameNumber` se colapsan en
        # un solo evento y se pierde uno de los dos partidos.
        game_number=_optional_int(game.get("gameNumber")) or 1,
    )


def _official_date(game: dict[str, Any], path: str) -> date:
    """Jornada a la que pertenece el partido.

    **No es la fecha UTC de `gameDate`.** Medido sobre un slate real: 2 de 9
    partidos empezaban a las 00:05 y 00:10 UTC del día siguiente y su
    `officialDate` era el día anterior. Derivar la fecha del timestamp habría
    archivado el 22% de los partidos de esa noche en el día equivocado, partiendo
    cada jornada en dos y descolocando el emparejamiento con el proveedor de
    odds, que sí razona por jornada.

    Si el proveedor dejara de mandarlo se cae a la fecha UTC, que es incorrecta
    para los nocturnos pero mejor que no tener evento.
    """
    raw = game.get("officialDate")
    if isinstance(raw, str):
        try:
            return date.fromisoformat(raw)
        except ValueError as exc:
            raise ShapeError(f"{path}.officialDate: fecha inválida: {raw!r}") from exc
    return parse_iso8601(require_str(game, "gameDate", path=path), path=path).date()


def _pitcher_name(side: dict[str, Any]) -> str | None:
    pitcher = side.get("probablePitcher")
    if isinstance(pitcher, dict):
        name = pitcher.get("fullName")
        if isinstance(name, str):
            return name
    return None


def _resolve_status(status: dict[str, Any]) -> str | None:
    """Estado real del partido, mirando `detailedState` antes que el abstracto.

    Un aplazado y un cancelado llegan como "Final" en `abstractGameState`, así
    que fiarse del abstracto convierte 42 partidos que no se jugaron en 42
    partidos terminados sin marcador.
    """
    detailed = status.get("detailedState")
    if isinstance(detailed, str):
        for marker, resolved in DETAILED_STATUS_OVERRIDES.items():
            if marker.lower() in detailed.lower():
                return resolved
    abstract = status.get("abstractGameState")
    return STATUS_MAP.get(abstract) if isinstance(abstract, str) else None


def _score_if_played(side: dict[str, Any], resolved_status: str | None) -> int | None:
    """Marcador solo si el partido se jugó. `None` en programados y cancelados."""
    if resolved_status in ("live", "final"):
        return _optional_int(side.get("score"))
    return None


def _optional_int(value: Any) -> int | None:
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else None


def _optional_str(value: Any) -> str | None:
    return str(value) if value is not None else None
