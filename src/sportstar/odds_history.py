"""Descarga, repara y audita el histórico de odds antes de dejarlo entrar.

Es el punto de entrada único al archivo de SBR. Encadena las tres piezas en el
único orden que tiene sentido:

    descargar (provider) -> reparar (normalizer) -> auditar (validation)

y **aborta si la auditoría bloquea**. Un histórico que no supera los checks no
entra al backtest con una advertencia en el log: no entra.

La secuencia importa. Detectar el emparejamiento no es lo mismo que validar el
resultado: el detector solo compara dos hipótesis entre sí, así que puede elegir
bien la menos mala de dos malas. La auditoría es la que dice si lo que salió es
un mercado de verdad.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .data.normalizers.sbr_archive import PairingDiagnosis, SbrGame, normalize
from .data.providers.sbr_archive import SbrArchiveProvider
from .validation.market_history import MarketHistoryAudit, audit, samples_from_american

DEFAULT_CACHE = Path("data/raw/odds_history")


class HistoryRejected(RuntimeError):
    """La auditoría bloqueó el histórico. No se usa."""


@dataclass(frozen=True, slots=True)
class LoadedHistory:
    games: list[SbrGame]
    diagnosis: PairingDiagnosis | None
    audit: MarketHistoryAudit


def load(
    sport: str = "mlb",
    *,
    seasons: range | None = None,
    cache_dir: Path | None = DEFAULT_CACHE,
    provider: SbrArchiveProvider | None = None,
) -> LoadedHistory:
    """Trae el histórico listo para backtestear, o falla explicando por qué no."""
    source = provider or SbrArchiveProvider(cache_dir=cache_dir)
    fetch = source.fetch(sport)
    games, diagnosis = normalize(fetch.payload)
    if seasons is not None:
        games = [g for g in games if g.season in seasons]

    decided = [g for g in games if g.home_won is not None]
    result = audit(
        samples_from_american(
            [
                (
                    g.home_open_american,
                    g.away_open_american,
                    g.home_close_american,
                    g.away_close_american,
                    bool(g.home_won),
                )
                for g in decided
            ]
        )
    )
    if result.report.blocking:
        detail = "\n".join(f"  [{f.check}] {f.message}" for f in result.report.blocking)
        raise HistoryRejected(
            f"el histórico de {sport} no supera la auditoría y no se usa:\n{detail}"
        )
    return LoadedHistory(games=games, diagnosis=diagnosis, audit=result)


def run(sport: str = "mlb") -> int:
    """Comando `sportstar odds-history`: descarga, repara, audita e informa."""
    print(f"descargando el archivo histórico de {sport} (puede tardar, son megabytes)...")
    try:
        loaded = load(sport)
    except HistoryRejected as exc:
        print(str(exc))
        return 1

    if loaded.diagnosis is not None:
        print()
        print(loaded.diagnosis.explain())
    print()
    print(loaded.audit.summary())
    seasons = sorted({g.season for g in loaded.games})
    print()
    print(f"temporadas: {seasons[0]}-{seasons[-1]}  ({len(loaded.games)} partidos)")
    return 0
