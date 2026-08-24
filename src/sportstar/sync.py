"""Captura periódica del mercado: una ejecución, un snapshot completo.

Es la pieza que convierte el proyecto en algo que acumula evidencia en vez de
mirar fotos sueltas.

**Por qué importa.** Con una sola jornada medí 28 observaciones de edge
estructural, cero de ellas por encima del umbral. La cota superior al 95% de la
frecuencia real es 10.7%: no puedo distinguir "nunca" de "el 5% de las veces",
y el 5% serían ~250 apuestas por temporada. Esa pregunta no se responde
pensando, se responde acumulando jornadas.

Cada ejecución guarda un payload **con marca de tiempo**, sin sobrescribir nada.
La secuencia de ficheros de un día *es* el movimiento de línea, y el último antes
del primer lanzamiento *es* el closing line — la única medición del sistema cuya
ventana no vuelve.

    python -m sportstar.cli sync

Pensado para ejecutarse varias veces al día desde un programador de tareas. Ver
`docs/CAPTURA_PROGRAMADA.md`.
"""

from __future__ import annotations

import gzip
import json
import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from .data.http import HttpError
from .data.providers import MlbStatsApiProvider, TheOddsApiProvider
from .data.providers.base import RawFetch

ODDS_API_KEY_ENV = "SPORTSTAR_ODDS_API_KEY"
SNAPSHOT_DIR = Path("data/raw/snapshots")

# Se piden dos días: el feed de odds va por delante del calendario y trae
# partidos de mañana. Sin el calendario de mañana, esos precios quedan
# huérfanos hasta el día siguiente.
SCHEDULE_DAYS_AHEAD = 1


def _write(payload: Any, kind: str, moment: datetime, out_dir: Path) -> Path:
    """Guarda un payload con marca de tiempo. Nunca sobrescribe.

    El nombre lleva el instante hasta el minuto: dos capturas del mismo día son
    dos ficheros, y su secuencia es el movimiento de línea.
    """
    day_dir = out_dir / f"{moment:%Y-%m-%d}"
    day_dir.mkdir(parents=True, exist_ok=True)
    path = day_dir / f"{kind}_{moment:%Y%m%dT%H%M}Z.json.gz"
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)
    return path


def _summarize(payload: Any) -> str:
    if isinstance(payload, list):
        return f"{len(payload)} eventos"
    if isinstance(payload, dict):
        games = sum(len(d.get("games", [])) for d in payload.get("dates", []))
        return f"{games} partidos"
    return type(payload).__name__


def sync_schedule(out_dir: Path, moment: datetime) -> tuple[int, RawFetch | None]:
    today = moment.date()
    print(f"\n[calendario] {today} .. {today + timedelta(days=SCHEDULE_DAYS_AHEAD)}")
    try:
        fetch = MlbStatsApiProvider().fetch_schedule_range(
            today, today + timedelta(days=SCHEDULE_DAYS_AHEAD)
        )
    except HttpError as exc:
        print(f"  ERROR: {exc}")
        return 1, None
    path = _write(fetch.payload, "schedule", moment, out_dir)
    print(f"  OK  {_summarize(fetch.payload)}  ->  {path.name}")
    return 0, fetch


def sync_odds(out_dir: Path, moment: datetime, api_key: str) -> tuple[int, RawFetch | None]:
    print("\n[odds] MLB moneyline, región us")
    try:
        fetch = TheOddsApiProvider(api_key).fetch_odds("mlb")
    except HttpError as exc:
        print(f"  ERROR: {exc}")
        if exc.provider_error_code == "OUT_OF_USAGE_CREDITS":
            print("  Cuota agotada. No es configuración: espera a que renueve.")
        elif exc.provider_error_code == "INVALID_KEY":
            print(f"  La key de {ODDS_API_KEY_ENV} fue rechazada.")
        return 1, None

    path = _write(fetch.payload, "odds", moment, out_dir)
    print(f"  OK  {_summarize(fetch.payload)}  ->  {path.name}")
    if fetch.quota_remaining is not None:
        # La cuota se mide, no se estima. El proveedor la manda en cada
        # respuesta y saber el consumo real decide la frecuencia de captura.
        print(f"  Cuota restante: {fetch.quota_remaining}")
    return 0, fetch


def run_sync(out_dir: Path | None = None, now: datetime | None = None) -> int:
    """Una captura completa. Devuelve 0 si todo fue bien."""
    target = out_dir or SNAPSHOT_DIR
    moment = now or datetime.now(UTC)

    print("=" * 62)
    print(f"  SYNC   {moment:%Y-%m-%d %H:%M}Z")
    print("=" * 62)

    failures, _ = sync_schedule(target, moment)

    api_key = os.environ.get(ODDS_API_KEY_ENV, "").strip()
    if not api_key:
        print(f"\n[odds] omitido: {ODDS_API_KEY_ENV} no está definida.")
        failures += 1
    else:
        odds_failures, _ = sync_odds(target, moment, api_key)
        failures += odds_failures

    print("\n" + "-" * 62)
    if failures:
        print(f"  {failures} fuente(s) fallaron. Las capturas previas siguen intactas.")
    else:
        print(f"  Snapshot guardado en {target / f'{moment:%Y-%m-%d}'}")
    return 1 if failures else 0


def load_snapshots(
    out_dir: Path | None = None, kind: str = "odds", day: date | None = None
) -> list[tuple[datetime, Any]]:
    """Lee las capturas en orden cronológico, con su instante.

    Devolver el timestamp junto al payload es lo que permite reconstruir el
    movimiento de línea: sin él, una secuencia de ficheros es solo un montón de
    fotos sin orden.
    """
    target = out_dir or SNAPSHOT_DIR
    if not target.exists():
        return []

    pattern = f"{day:%Y-%m-%d}/{kind}_*.json.gz" if day else f"*/{kind}_*.json.gz"
    snapshots = []
    for path in sorted(target.glob(pattern)):
        stamp = datetime.strptime(path.stem.split("_")[-1], "%Y%m%dT%H%MZ").replace(tzinfo=UTC)
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            snapshots.append((stamp, json.load(handle)))
    return snapshots
