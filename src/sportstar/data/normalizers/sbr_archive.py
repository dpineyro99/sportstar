"""Normaliza el archivo histórico de SBR — y repara su bug de emparejamiento.

El bug
------
El volcado publicado por `flancast90/sportsbookreview-scraper` **no es usable tal
cual**: cada fila mezcla dos partidos distintos. El origen está en tres líneas de
`scrapers/sportsbookreview.py`::

    progress = df.iterrows()
    next(progress)                       # <- salta una fila de más
    for (i1, row), (i2, next_row) in self._pairwise(progress):

La tabla de SBR trae **dos filas por partido** (visitante, luego local). El
DataFrame ya venía recortado con `dfs[0][1:]`, o sea con la cabecera fuera, así
que ese `next(progress)` no salta la cabecera: salta la fila del visitante del
primer partido. A partir de ahí el emparejamiento va corrido una posición, y
cada fila publicada junta el **local del partido k** con el **visitante del
partido k+1** —además intercambiando las etiquetas local/visitante—.

Cómo se ve
----------
En la jornada del 2011-04-01 el volcado publica "Phillies @ Pirates 5-6". La
realidad fueron dos partidos distintos: Astros @ Phillies 4-5 y Pirates @ Cubs
6-3. Los Phillies sí marcaron 5 y los Pirates sí marcaron 6; lo que está roto es
que estén en la misma fila.

Los síntomas agregados son inconfundibles y sirven de detector:

==========================  =============  ==============
métrica                     tal cual       tras corregir
==========================  =============  ==============
"empates" (imposibles)      2.653          18
sobre-redondeo p1 / p99     -18% / +24%    +1,5% / +4,8%
% victorias local           48,2%          53,5%
Brier del cierre            0,2462         0,2404
==========================  =============  ==============

El 53,5% de victorias locales es el valor real de MLB, y el sobre-redondeo
negativo —dos favoritos en un mercado de dos vías— desaparece. Contrastado
además contra la MLB Stats API: 61 de 63 partidos casan exactos en
(local, visitante, marcador); los dos que no son un suspendido sin marcador y
una segunda parte de doble jornada.

Por qué se detecta en vez de aplicarse a ciegas
-----------------------------------------------
Si el upstream arregla su scraper, aplicar la corrección de oficio **crearía** la
corrupción que hoy repara. Por eso `detect_pairing` mide la coherencia de las dos
hipótesis sobre los propios datos —empates imposibles y sobre-redondeo fuera de
rango— y se queda con la que sea coherente; si ninguna lo es, o si las dos lo
son por igual, aborta en vez de adivinar.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Any

from ...core.odds import american_to_implied
from .errors import ShapeError, require_list

PROVIDER = "sbr-archive"

# Códigos de tres letras que el scraper original dejó sin traducir, mapeados al
# apodo que usa el resto del volcado. La resolución al catálogo la sigue haciendo
# `resolution/`, que además sabe de renombres de franquicia (Florida -> Miami
# Marlins en 2012, Cleveland Indians -> Guardians en 2022).
TEAM_ALIASES: dict[str, str] = {
    "SFO": "Giants",
    "SFG": "Giants",
    "TAM": "Rays",
    "KAN": "Royals",
    "CUB": "Cubs",
    "SDG": "Padres",
    "LOS": "Dodgers",
    "BRS": "Red Sox",
}

# Rango de sobre-redondeo plausible en un moneyline de dos vías. MLB se mueve en
# 2-5%; se abre la horquilla generosamente porque esto es un detector de datos
# rotos, no un control de calidad fino.
PLAUSIBLE_OVERROUND = (0.0, 0.15)

# Un empate en MLB es prácticamente imposible: solo ocurre en partidos
# suspendidos que nunca se reanudan. Por encima de este umbral, el emparejamiento
# está roto.
MAX_TIE_RATE = 0.02

# Margen mínimo entre hipótesis para considerar que una gana de forma clara.
MIN_DECISION_MARGIN = 0.10


class Pairing(StrEnum):
    """Cómo hay que leer las filas del volcado."""

    #: Las filas ya vienen bien emparejadas (upstream arreglado).
    AS_PUBLISHED = "as_published"
    #: Filas corridas una posición: hay que deshacer el desplazamiento.
    SHIFTED = "shifted"


@dataclass(frozen=True, slots=True)
class SbrGame:
    """Un partido histórico con moneyline de apertura y cierre.

    Sin `observed_at`: el archivo no dice **cuándo** se observó cada precio, solo
    que uno es la apertura y otro el cierre. Inventar marcas de tiempo aquí sería
    la vía más directa a un backtest con leakage, así que no se inventan; el
    consumidor decide qué significa "antes del partido" con lo que sí sabe.
    """

    season: int
    game_date: date
    home_team_raw: str
    away_team_raw: str
    home_score: int | None
    away_score: int | None
    home_open_american: float | None
    away_open_american: float | None
    home_close_american: float | None
    away_close_american: float | None

    @property
    def played(self) -> bool:
        return self.home_score is not None and self.away_score is not None

    @property
    def home_won(self) -> bool | None:
        if not self.played:
            return None
        assert self.home_score is not None and self.away_score is not None
        if self.home_score == self.away_score:
            return None
        return self.home_score > self.away_score

    def closing_overround(self) -> float | None:
        return _overround(self.home_close_american, self.away_close_american)


@dataclass(frozen=True, slots=True)
class PairingDiagnosis:
    """Evidencia numérica de por qué se eligió un emparejamiento."""

    chosen: Pairing
    incoherence: dict[Pairing, float]
    tie_rate: dict[Pairing, float]
    bad_overround_rate: dict[Pairing, float]

    def explain(self) -> str:
        lines = [f"emparejamiento elegido: {self.chosen.value}"]
        for pairing in Pairing:
            lines.append(
                f"  {pairing.value:<13} incoherencia={self.incoherence[pairing]:.3f} "
                f"(empates={self.tie_rate[pairing]:.3f}, "
                f"sobre-redondeo fuera de rango={self.bad_overround_rate[pairing]:.3f})"
            )
        return "\n".join(lines)


class PairingUndecidable(ShapeError):
    """Ninguna hipótesis de emparejamiento produce datos coherentes."""


def _num(value: Any) -> float | None:
    """Devuelve el número, o `None` si no lo es.

    El volcado usa la cadena `"NL"` (no line) para precios que no existieron, y
    `None` para filas truncadas. Ni uno ni otro es un cero: un moneyline de 0 no
    existe, y tratarlo como número mete un precio imposible en el histórico.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _score(value: Any) -> int | None:
    number = _num(value)
    return None if number is None else int(number)


def _overround(home: float | None, away: float | None) -> float | None:
    if home is None or away is None or home == 0.0 or away == 0.0:
        return None
    return american_to_implied(home) + american_to_implied(away) - 1.0


def _parse_date(value: Any, *, path: str) -> date:
    number = _num(value)
    if number is None:
        raise ShapeError(f"{path}: fecha no numérica, llegó {value!r}")
    text = f"{int(number):08d}"
    try:
        return date(int(text[:4]), int(text[4:6]), int(text[6:8]))
    except ValueError as exc:
        raise ShapeError(f"{path}: fecha inválida {text!r} ({exc})") from exc


def _team(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    name = value.strip()
    return TEAM_ALIASES.get(name, name)


def _build(rows: list[dict[str, Any]], pairing: Pairing) -> list[SbrGame]:
    """Materializa los partidos según la hipótesis de emparejamiento.

    En `SHIFTED`, el partido verdadero se reconstruye tomando el lado *visitante*
    de la fila `i` como **local** y el lado *local* de la fila `i-1` como
    **visitante** —que es exactamente lo que deshace el `next()` de más—. Se
    cruzan solo filas de la misma temporada: las tablas se concatenaron por
    temporada, así que en la frontera no hay continuidad que deshacer. El coste es
    perder un partido por temporada, el primero, cuya fila de visitante el
    scraper original consumió y no está en el volcado.
    """
    games: list[SbrGame] = []
    for index, row in enumerate(rows):
        if pairing is Pairing.AS_PUBLISHED:
            home_src, away_src, home_prefix, away_prefix = row, row, "home", "away"
        else:
            if index == 0 or rows[index - 1].get("season") != row.get("season"):
                continue
            home_src, away_src, home_prefix, away_prefix = row, rows[index - 1], "away", "home"

        season = _num(row.get("season"))
        if season is None:
            raise ShapeError(f"fila {index}: falta 'season', llegó {row.get('season')!r}")

        home_team = _team(home_src.get(f"{home_prefix}_team"))
        away_team = _team(away_src.get(f"{away_prefix}_team"))
        if home_team is None or away_team is None:
            continue

        games.append(
            SbrGame(
                season=int(season),
                game_date=_parse_date(row.get("date"), path=f"fila {index}.date"),
                home_team_raw=home_team,
                away_team_raw=away_team,
                home_score=_score(home_src.get(f"{home_prefix}_final")),
                away_score=_score(away_src.get(f"{away_prefix}_final")),
                home_open_american=_num(home_src.get(f"{home_prefix}_open_ml")) or None,
                away_open_american=_num(away_src.get(f"{away_prefix}_open_ml")) or None,
                home_close_american=_num(home_src.get(f"{home_prefix}_close_ml")) or None,
                away_close_american=_num(away_src.get(f"{away_prefix}_close_ml")) or None,
            )
        )
    return games


def _incoherence(games: list[SbrGame]) -> tuple[float, float, float]:
    """Mide cuán imposible es un conjunto de partidos. Devuelve (total, empates, vig)."""
    scored = [g for g in games if g.played]
    priced = [o for o in (g.closing_overround() for g in games) if o is not None]

    tie_rate = (
        sum(1 for g in scored if g.home_score == g.away_score) / len(scored) if scored else 1.0
    )
    low, high = PLAUSIBLE_OVERROUND
    bad_vig_rate = sum(1 for o in priced if not low <= o <= high) / len(priced) if priced else 1.0
    return tie_rate + bad_vig_rate, tie_rate, bad_vig_rate


def detect_pairing(rows: list[dict[str, Any]]) -> PairingDiagnosis:
    """Decide cómo hay que leer las filas, midiendo el resultado de cada hipótesis.

    No mira la versión del upstream ni ninguna bandera: mira si los datos que
    salen son físicamente posibles. Un mercado de dos vías no puede tener dos
    favoritos, y MLB no tiene empates.
    """
    incoherence: dict[Pairing, float] = {}
    ties: dict[Pairing, float] = {}
    vigs: dict[Pairing, float] = {}
    for pairing in Pairing:
        total, tie_rate, bad_vig = _incoherence(_build(rows, pairing))
        incoherence[pairing], ties[pairing], vigs[pairing] = total, tie_rate, bad_vig

    best = min(Pairing, key=lambda p: incoherence[p])
    worst = max(Pairing, key=lambda p: incoherence[p])
    diagnosis = PairingDiagnosis(
        chosen=best, incoherence=incoherence, tie_rate=ties, bad_overround_rate=vigs
    )

    if ties[best] > MAX_TIE_RATE:
        raise PairingUndecidable(
            "ninguna hipótesis de emparejamiento produce datos coherentes: la mejor "
            f"deja un {ties[best]:.1%} de empates, imposible en MLB.\n{diagnosis.explain()}"
        )
    if incoherence[worst] - incoherence[best] < MIN_DECISION_MARGIN:
        raise PairingUndecidable(
            "las dos hipótesis de emparejamiento son igual de plausibles, así que "
            "elegir una sería adivinar.\n" + diagnosis.explain()
        )
    return diagnosis


def normalize(
    payload: Any, *, pairing: Pairing | None = None
) -> tuple[list[SbrGame], PairingDiagnosis | None]:
    """Convierte el volcado en partidos, corrigiendo el emparejamiento si toca.

    Con `pairing=None` —lo normal— se detecta. Pasarlo explícito es para tests y
    para forzar una lectura cuando se sabe algo que los datos no dicen.
    """
    rows = require_list(payload, path="payload")
    typed: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ShapeError(f"payload[{index}]: se esperaba un objeto, llegó {type(row).__name__}")
        typed.append(row)

    if pairing is not None:
        return _build(typed, pairing), None
    diagnosis = detect_pairing(typed)
    return _build(typed, diagnosis.chosen), diagnosis
