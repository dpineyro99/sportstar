"""El histórico de odds convertido al dominio del backtest.

Aquí se resuelve el punto más delicado de toda la fase: **el archivo de SBR no
tiene horas, solo fechas**. El backtest necesita un orden temporal, y fabricarlo
mal es la forma más silenciosa de contaminarlo.

La convención, deliberadamente conservadora:

- el **resultado** de un partido del día D se considera conocido a las `D 23:59Z`
- la **apuesta** de un partido del día D se decide a las `D 00:00Z`

Consecuencia directa: ningún partido del día D influye en las predicciones del
día D, ni siquiera los que se jugaron por la tarde y cuyo resultado sí se sabía
antes del partido de la noche. Eso **tira información real** —en MLB hay muchas
tardes con resultados ya cerrados—, y se tira a propósito: errar hacia "lo
supimos después" como mucho desaprovecha un dato, mientras que errar hacia "lo
supimos antes" produce leakage, y el leakage no da error, da buenos resultados.

Las **dobles jornadas** son reales y son 341 en el archivo: dos partidos entre
los mismos equipos el mismo día. Se distinguen con `archive_sequence`, porque sin
él colapsan en un solo evento y el check de duplicados de `sanity.py` —con razón—
bloquea el backtest entero.

Los ids de equipo se derivan del propio vocabulario del fichero, ordenados
alfabéticamente, para que sean estables entre ejecuciones: un id que cambia entre
corridas hace irreproducible cualquier resultado que dependa del orden.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time

from ..core.odds import american_to_decimal, american_to_implied
from ..data.normalizers.sbr_archive import SbrGame
from ..features.elo import GameResult

#: Hora a la que se considera conocido el resultado de un partido.
RESULT_KNOWN_AT = time(23, 59, tzinfo=UTC)
#: Hora a la que se decide la apuesta. Antes de cualquier partido de ese día.
DECISION_AT = time(0, 0, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class MarketPrices:
    """Los dos precios que el archivo conoce de un lado del mercado."""

    open_american: float
    close_american: float

    @property
    def open_decimal(self) -> float:
        return american_to_decimal(self.open_american)

    @property
    def close_decimal(self) -> float:
        return american_to_decimal(self.close_american)

    @property
    def open_implied(self) -> float:
        return american_to_implied(self.open_american)

    @property
    def close_implied(self) -> float:
        return american_to_implied(self.close_american)


@dataclass(frozen=True, slots=True)
class HistoricalGame:
    """Un partido del histórico, listo para replay point-in-time."""

    season: int
    game_date: datetime
    home_team_id: int
    away_team_id: int
    home_team: str
    away_team: str
    home_score: int
    away_score: int
    home: MarketPrices
    away: MarketPrices
    #: Abridores previstos, cuando el cruce con la MLB Stats API los encontró.
    #: `None` significa "no se sabe", nunca "no hay": la diferencia importa,
    #: porque un modelo que trate el desconocimiento como un valor neutro está
    #: inventando información.
    home_pitcher_id: int | None = None
    away_pitcher_id: int | None = None
    #: Ordinal dentro de (fecha, local, visitante). **No es el número de partido
    #: oficial de MLB**: el archivo no lo trae, y su orden no coincide
    #: necesariamente con el de la liga —comprobado en la doble jornada del
    #: 2011-04-09, donde el archivo lista primero el que la MLB numera como 2—.
    #: Solo sirve para distinguir las dos mitades de una doble jornada, que es
    #: para lo que se usa: sin él, 341 pares de partidos reales colapsan en uno.
    archive_sequence: int = 1

    @property
    def has_starters(self) -> bool:
        return self.home_pitcher_id is not None and self.away_pitcher_id is not None

    @property
    def decided_at(self) -> datetime:
        """Cuándo se toma la decisión. Nada de este día se conoce todavía."""
        return datetime.combine(self.game_date.date(), DECISION_AT)

    @property
    def observed_at(self) -> datetime:
        """Cuándo se conoce el resultado."""
        return datetime.combine(self.game_date.date(), RESULT_KNOWN_AT)

    @property
    def home_won(self) -> bool:
        return self.home_score > self.away_score

    @property
    def result(self) -> GameResult:
        return GameResult(
            season=self.season,
            home_team_id=self.home_team_id,
            away_team_id=self.away_team_id,
            home_score=self.home_score,
            away_score=self.away_score,
            observed_at=self.observed_at,
        )

    def prices(self, side: str) -> MarketPrices:
        return self.home if side == "home" else self.away

    def won(self, side: str) -> bool:
        return self.home_won if side == "home" else not self.home_won


def team_ids(games: list[SbrGame]) -> dict[str, int]:
    """Asigna un id estable a cada equipo, por orden alfabético.

    Alfabético y no orden de aparición: el orden de aparición depende de por
    dónde se empiece a leer el fichero, y un id que cambia entre ejecuciones hace
    irreproducible cualquier resultado que dependa del orden.
    """
    names = sorted({name for g in games for name in (g.home_team_raw, g.away_team_raw)})
    return {name: index for index, name in enumerate(names)}


def to_historical_games(games: list[SbrGame]) -> list[HistoricalGame]:
    """Convierte lo que sirve del archivo. Descarta lo que no se puede usar.

    Se cae un partido si le falta el marcador, si quedó en empate —en MLB eso es
    un suspendido que no se reanudó, no un resultado— o si le falta cualquiera de
    los cuatro precios. Un partido a medias no se completa con supuestos: se
    queda fuera y se cuenta.
    """
    ids = team_ids(games)
    out: list[HistoricalGame] = []
    seen: dict[tuple[object, str, str], int] = {}

    for game in games:
        if game.home_won is None:
            continue
        prices = (
            game.home_open_american,
            game.home_close_american,
            game.away_open_american,
            game.away_close_american,
        )
        if any(p is None for p in prices):
            continue
        home_open, home_close, away_open, away_close = (float(p) for p in prices)  # type: ignore[arg-type]
        assert game.home_score is not None and game.away_score is not None

        key = (game.game_date, game.home_team_raw, game.away_team_raw)
        seen[key] = seen.get(key, 0) + 1

        out.append(
            HistoricalGame(
                season=game.season,
                game_date=datetime.combine(game.game_date, DECISION_AT),
                home_team_id=ids[game.home_team_raw],
                away_team_id=ids[game.away_team_raw],
                home_team=game.home_team_raw,
                away_team=game.away_team_raw,
                home_score=game.home_score,
                away_score=game.away_score,
                home=MarketPrices(home_open, home_close),
                away=MarketPrices(away_open, away_close),
                archive_sequence=seen[key],
            )
        )

    out.sort(key=lambda g: (g.game_date, g.home_team_id, g.away_team_id, g.archive_sequence))
    return out
