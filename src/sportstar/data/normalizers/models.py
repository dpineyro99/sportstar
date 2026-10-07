"""Estructuras canónicas que produce la normalización.

Deliberadamente **no** contienen ids del catálogo: un normalizador no empareja.
Lleva `*_raw` con el texto tal cual vino, y el emparejamiento lo hace después
`resolution/`, que sabe registrar lo que no resuelve en vez de descartarlo.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass(frozen=True, slots=True)
class NormalizedEvent:
    provider: str
    provider_event_id: str
    sport_key: str
    start_time: datetime
    # Fecha de calendario a la que pertenece el partido, que NO es la fecha UTC
    # de `start_time`. Un partido de noche que empieza a las 00:05 UTC pertenece
    # a la jornada del día anterior. Ignorarlo parte cada noche en dos fechas y
    # descoloca el emparejamiento con el proveedor de odds.
    official_date: date | None = None
    home_team_raw: str = ""
    away_team_raw: str = ""
    status: str | None = None
    # Tipo de competición. Distinguirlo no es cosmético: la pretemporada y las
    # exhibiciones se juegan con prospectos y contra rivales que ni siquiera son
    # equipos de la liga, así que sus resultados no dicen nada de la fuerza real.
    game_type: str | None = None
    home_score: int | None = None
    away_score: int | None = None
    venue_raw: str | None = None
    home_probable_pitcher_raw: str | None = None
    away_probable_pitcher_raw: str | None = None
    provider_home_team_id: str | None = None
    provider_away_team_id: str | None = None
    game_number: int = 1


@dataclass(frozen=True, slots=True)
class NormalizedPrice:
    """Un precio observado, todavía sin emparejar con el catálogo."""

    provider: str
    provider_event_id: str
    book_key: str
    market_type: str  # moneyline | spread | total | ...
    period: str
    side_raw: str  # el texto del proveedor: nombre de equipo, "Over", "Under"
    price_american: float
    line: float | None
    last_update: datetime | None


@dataclass(slots=True)
class NormalizationResult:
    """Resultado de normalizar un payload completo.

    Los errores por elemento se acumulan en vez de abortar: un evento con formato
    raro no debe tirar el slate entero. Un error de forma en el nivel superior sí
    aborta, porque significa que el proveedor cambió y nada de lo que sigue es
    fiable.
    """

    events: list[NormalizedEvent] = field(default_factory=list)
    prices: list[NormalizedPrice] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    skipped_books: set[str] = field(default_factory=set)

    @property
    def counters(self) -> dict[str, int]:
        return {
            "received": len(self.events),
            "prices": len(self.prices),
            "item_errors": len(self.errors),
        }
