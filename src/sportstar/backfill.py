"""Descarga de histórico de MLB a ficheros locales.

Existe porque el entorno donde se desarrolla el sistema no tiene salida a
`statsapi.mlb.com`, y porque un histórico de temporada no se resuelve pegando
payloads a mano: son miles de partidos.

    python -m sportstar.cli backfill --start 2024-03-20 --end 2024-10-01

Escribe un fichero comprimido por mes en `data/raw/mlb/`. Esos ficheros se
commitean, y con eso el histórico viaja por git en vez de por la red.

Los payloads se guardan **íntegros**, sin normalizar. Es la misma decisión que
`raw_payloads` en la base: cuando un normalizador tenga un bug se reprocesa todo
sin volver a descargar nada.
"""

from __future__ import annotations

import gzip
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .data.http import HttpError
from .data.providers import MlbStatsApiProvider

RAW_DIR = Path("data/raw/mlb")


def month_ranges(start: date, end: date) -> list[tuple[date, date]]:
    """Parte un rango en tramos mensuales.

    Un tramo por mes en vez de uno por día: la API acepta rangos, así que una
    temporada son seis peticiones y no ciento ochenta.
    """
    ranges: list[tuple[date, date]] = []
    cursor = start.replace(day=1)
    while cursor <= end:
        next_month = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
        last_day = next_month - timedelta(days=1)
        ranges.append((max(cursor, start), min(last_day, end)))
        cursor = next_month
    return ranges


def run_backfill(start: date, end: date, out_dir: Path | None = None) -> int:
    """Descarga el histórico mes a mes. Reanudable: salta lo ya descargado."""
    target = out_dir or RAW_DIR
    target.mkdir(parents=True, exist_ok=True)
    provider = MlbStatsApiProvider()

    print("=" * 62)
    print(f"  BACKFILL MLB   {start} .. {end}")
    print("=" * 62)
    print(f"\n  Destino: {target}\n")

    failures = 0
    total_games = 0

    for range_start, range_end in month_ranges(start, end):
        path = target / f"schedule_{range_start:%Y-%m}.json.gz"
        if path.exists():
            print(f"  {range_start:%Y-%m}  ya descargado, se salta")
            continue

        try:
            fetch = provider.fetch_schedule_range(range_start, range_end)
        except HttpError as exc:
            print(f"  {range_start:%Y-%m}  ERROR: {exc}")
            if exc.status is None:
                print("      Sin respuesta del host. ¿Hay salida a statsapi.mlb.com?")
            failures += 1
            continue

        payload = fetch.payload
        games = sum(len(d.get("games", [])) for d in payload.get("dates", []))
        total_games += games

        # Comprimido: una temporada completa sin comprimir son decenas de MB, y
        # esto acaba en un repositorio git.
        with gzip.open(path, "wt", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)

        size_kb = path.stat().st_size / 1024
        print(f"  {range_start:%Y-%m}  {games:>4} partidos  {size_kb:>7.0f} KB  -> {path.name}")

    print("\n" + "-" * 62)
    if failures:
        print(f"  {failures} mes(es) fallaron. Vuelve a ejecutar: se reanuda donde lo dejó.")
        return 1
    print(f"  {total_games} partidos descargados.")
    print("  Ahora:  git add data/raw && git commit -m 'histórico MLB' && git push")
    return 0


def load_backfill(out_dir: Path | None = None) -> list[dict[str, Any]]:
    """Lee los payloads descargados, en orden cronológico.

    Acepta `.json.gz` y `.json` sin comprimir. Lo segundo no es por comodidad:
    la vía de respaldo para conseguir el histórico es descargar las URLs desde
    un navegador y subir los ficheros por la web de GitHub, sin instalar nada.
    Ese camino produce JSON plano, y exigir compresión lo cerraría por un
    detalle de formato.
    """
    target = out_dir or RAW_DIR
    if not target.exists():
        return []

    payloads: list[dict[str, Any]] = []
    for path in sorted(target.glob("schedule_*.json*")):
        if path.suffix == ".gz":
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                payloads.append(json.load(handle))
        elif path.suffix == ".json":
            payloads.append(json.loads(path.read_text(encoding="utf-8")))
    return payloads
