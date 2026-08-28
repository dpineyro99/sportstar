"""Descarga y caché del histórico de lanzadores. Punto de entrada único.

Dos pasos, en este orden:

1. **calendario por temporada** con `probablePitcher` — 11 peticiones, ~1s cada
   una. De aquí sale quién abrió cada partido y, de paso, la lista de abridores.
2. **game log por (abridor, temporada)** — ~310 abridores por temporada, así que
   ~3.400 peticiones contra una API gratuita y sin key.

El segundo paso es el caro, y por eso se cachea a disco en forma ya normalizada.
Volver a bajarlo son unos minutos; hacerlo en cada arranque sería maleducado con
una API pública que no cobra nada.

La caché guarda el resultado **normalizado**, no el payload crudo, que es la
única concesión al pragmatismo en todo el proyecto: son 3.400 respuestas y
guardarlas íntegras multiplicaría el tamaño por veinte para poder reprocesar un
normalizador de cuarenta líneas. Si el normalizador cambia, se vuelve a bajar.
"""

from __future__ import annotations

import gzip
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .data.http import HttpClient
from .data.normalizers.mlb_pitchers import (
    PitchingAppearance,
    ProbableStarters,
    normalize_game_log,
    normalize_probable_starters,
)
from .data.providers.mlb_stats_api import MlbStatsApiProvider

DEFAULT_CACHE = Path("data/raw/mlb/pitchers")

#: Ventana de fechas por temporada. Generosa a ambos lados: marzo por las
#: aperturas en el extranjero, noviembre por una Serie Mundial larga.
SEASON_START = (3, 1)
SEASON_END = (11, 15)


@dataclass(frozen=True, slots=True)
class PitcherHistory:
    """Todo lo que se sabe de lanzadores en un rango de temporadas."""

    starters: list[ProbableStarters]
    appearances: list[PitchingAppearance]

    @property
    def pitcher_ids(self) -> set[int]:
        return {a.pitcher_id for a in self.appearances}


def _season_window(season: int) -> tuple[date, date]:
    return date(season, *SEASON_START), date(season, *SEASON_END)


def _starters_path(cache_dir: Path, season: int) -> Path:
    return cache_dir / f"starters_{season}.json.gz"


def _appearances_path(cache_dir: Path, season: int) -> Path:
    return cache_dir / f"appearances_{season}.json.gz"


def _write(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(json.dumps(rows).encode("utf-8")))


def _read(path: Path) -> list[dict[str, object]] | None:
    if not path.exists():
        return None
    data = json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))
    return data if isinstance(data, list) else None


def load_season(
    season: int,
    *,
    cache_dir: Path = DEFAULT_CACHE,
    provider: MlbStatsApiProvider | None = None,
    progress: bool = False,
) -> PitcherHistory:
    """Trae una temporada, de la caché si está, de la API si no.

    Las dos mitades se cachean **por separado** a propósito: los abridores son
    una petición y las apariciones son trescientas. Acoplarlas significaba que
    cambiar un campo del lado barato obligaba a volver a bajar el lado caro, y
    esa factura ya se pagó una vez.
    """
    source: MlbStatsApiProvider | None = provider

    def api() -> MlbStatsApiProvider:
        nonlocal source
        if source is None:
            source = MlbStatsApiProvider(HttpClient(timeout=30.0))
        return source

    cached = _read(_starters_path(cache_dir, season))
    if cached is not None:
        starters = [_starters_from_json(row) for row in cached]
    else:
        start, end = _season_window(season)
        starters = normalize_probable_starters(api().fetch_schedule_range(start, end).payload)
        _write(_starters_path(cache_dir, season), [_starters_to_json(s) for s in starters])

    cached = _read(_appearances_path(cache_dir, season))
    if cached is not None:
        return PitcherHistory(
            starters=starters,
            appearances=[_appearance_from_json(row) for row in cached],
        )

    ids = sorted(
        {p for s in starters for p in (s.home_pitcher_id, s.away_pitcher_id) if p is not None}
    )
    if progress:
        print(f"  {season}: {len(starters)} partidos, {len(ids)} abridores distintos")

    appearances: list[PitchingAppearance] = []
    for index, pitcher_id in enumerate(ids, start=1):
        payload = api().fetch_pitcher_game_log(pitcher_id, season).payload
        appearances.extend(normalize_game_log(payload, pitcher_id))
        if progress and index % 50 == 0:
            print(f"    {index}/{len(ids)} game logs")

    _write(_appearances_path(cache_dir, season), [_appearance_to_json(a) for a in appearances])
    return PitcherHistory(starters=starters, appearances=appearances)


def load(
    seasons: range,
    *,
    cache_dir: Path = DEFAULT_CACHE,
    provider: MlbStatsApiProvider | None = None,
    progress: bool = False,
) -> PitcherHistory:
    """Trae varias temporadas y las concatena."""
    starters: list[ProbableStarters] = []
    appearances: list[PitchingAppearance] = []
    for season in seasons:
        history = load_season(season, cache_dir=cache_dir, provider=provider, progress=progress)
        starters.extend(history.starters)
        appearances.extend(history.appearances)
    return PitcherHistory(starters=starters, appearances=appearances)


# --- Serialización de la caché ------------------------------------------------
# Explícita y por campos en vez de `asdict`: si alguien añade un campo, el fallo
# es un `KeyError` al leer una caché vieja, no un dato silenciosamente a cero.


def _as_int(row: dict[str, object], key: str) -> int:
    value = row[key]
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError(f"caché corrupta: {key!r} no es un número, es {value!r}")
    return int(value)


def _as_optional_int(row: dict[str, object], key: str) -> int | None:
    return None if row[key] is None else _as_int(row, key)


def _starters_to_json(s: ProbableStarters) -> dict[str, object]:
    return {
        "d": s.official_date.isoformat(),
        "pk": s.game_pk,
        "gn": s.game_number,
        "h": s.home_team_id,
        "a": s.away_team_id,
        "hp": s.home_pitcher_id,
        "ap": s.away_pitcher_id,
        "hs": s.home_score,
        "as": s.away_score,
    }


def _starters_from_json(row: dict[str, object]) -> ProbableStarters:
    return ProbableStarters(
        official_date=date.fromisoformat(str(row["d"])),
        game_pk=_as_int(row, "pk"),
        game_number=_as_int(row, "gn"),
        home_team_id=_as_int(row, "h"),
        away_team_id=_as_int(row, "a"),
        home_pitcher_id=_as_optional_int(row, "hp"),
        away_pitcher_id=_as_optional_int(row, "ap"),
        home_score=_as_optional_int(row, "hs"),
        away_score=_as_optional_int(row, "as"),
    )


def _appearance_to_json(a: PitchingAppearance) -> dict[str, object]:
    return {
        "d": a.game_date.isoformat(),
        "p": a.pitcher_id,
        "s": a.is_start,
        "o": a.outs,
        "er": a.earned_runs,
        "k": a.strikeouts,
        "bb": a.walks,
        "h": a.hits,
        "hr": a.home_runs,
        "bf": a.batters_faced,
    }


def _appearance_from_json(row: dict[str, object]) -> PitchingAppearance:
    return PitchingAppearance(
        game_date=date.fromisoformat(str(row["d"])),
        pitcher_id=_as_int(row, "p"),
        is_start=bool(row["s"]),
        outs=_as_int(row, "o"),
        earned_runs=_as_int(row, "er"),
        strikeouts=_as_int(row, "k"),
        walks=_as_int(row, "bb"),
        hits=_as_int(row, "h"),
        home_runs=_as_int(row, "hr"),
        batters_faced=_as_int(row, "bf"),
    )


def run(start: int = 2011, end: int = 2021) -> int:
    """Comando `sportstar pitchers`: descarga y cachea el histórico."""
    print(f"descargando histórico de lanzadores {start}-{end}...")
    print("la primera vez son ~3.400 peticiones y varios minutos. Después, caché.")
    history = load(range(start, end + 1), progress=True)
    starts = sum(a.is_start for a in history.appearances)
    print()
    print(f"partidos con abridores : {sum(s.complete for s in history.starters)}")
    print(f"lanzadores distintos   : {len(history.pitcher_ids)}")
    print(f"apariciones            : {len(history.appearances)} ({starts} aperturas)")
    return 0
