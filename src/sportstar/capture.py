"""Captura de fixtures reales desde los proveedores.

Los normalizadores están escritos contra documentación, no contra respuestas
verificadas. Este comando cierra ese hueco: trae las respuestas reales, las
guarda como fixtures y deja que la suite de tests las valide.

    python -m sportstar.cli capture
    pytest tests/data -q

Si algún test falla, el mensaje dice qué clave faltaba y qué llegó en su lugar.
Eso *es* la verificación del esquema.

**Nunca se guarda la URL de la petición**, solo el cuerpo de la respuesta: la API
key de The Odds API viaja en la query string y acabaría commiteada.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from .data.http import HttpError
from .data.providers import MlbStatsApiProvider, TheOddsApiProvider
from .data.providers.base import RawFetch

ODDS_API_KEY_ENV = "SPORTSTAR_ODDS_API_KEY"
FIXTURE_DIR = Path(__file__).resolve().parents[2] / "tests" / "data" / "fixtures"


def _write(fetch: RawFetch, filename: str, fixture_dir: Path) -> Path:
    path = fixture_dir / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    # Solo el payload. Ni URL, ni cabeceras, ni nada que pueda llevar la key.
    path.write_text(json.dumps(fetch.payload, indent=2, ensure_ascii=False) + "\n", "utf-8")
    return path


def _summarize(payload: object) -> str:
    if isinstance(payload, list):
        return f"{len(payload)} elemento(s)"
    if isinstance(payload, dict):
        return f"objeto con claves {sorted(payload)[:8]}"
    return type(payload).__name__


def capture_mlb_schedule(fixture_dir: Path) -> int:
    print("\n[mlb-stats-api] calendario de hoy ...")
    try:
        fetch = MlbStatsApiProvider().fetch_schedule(datetime.now(UTC).date())
    except HttpError as exc:
        print(f"  ERROR: {exc}")
        if exc.status is None:
            print("  Sin respuesta del host. Suele ser la política de red del entorno.")
        return 1

    path = _write(fetch, "mlb_stats_api_schedule.json", fixture_dir)
    print(f"  OK  {_summarize(fetch.payload)} -> {path.name}")
    return 0


def capture_odds(fixture_dir: Path, api_key: str) -> int:
    print("\n[the-odds-api] verificando la key con /sports (no gasta cuota de odds) ...")
    provider = TheOddsApiProvider(api_key)
    try:
        sports = provider.fetch_sports()
    except HttpError as exc:
        print(f"  ERROR: {exc}")
        # El código del proveedor distingue causas que se arreglan de formas muy
        # distintas: una es un typo, otra es esperar a que renueve la cuota.
        if exc.provider_error_code == "INVALID_KEY":
            print(f"  La key de {ODDS_API_KEY_ENV} fue rechazada por el proveedor.")
            print("  Revisa que sea la key literal, sin espacios ni comillas.")
        elif exc.provider_error_code == "OUT_OF_USAGE_CREDITS":
            print("  Cuota agotada. No es un problema de configuración: renueva o espera.")
        elif exc.status is None:
            print("  Sin respuesta del host. Suele ser la política de red del entorno.")
        return 1
    print(f"  OK  {_summarize(sports.payload)}")

    print("\n[the-odds-api] odds de MLB (h2h) ...")
    try:
        fetch = provider.fetch_odds("mlb")
    except HttpError as exc:
        print(f"  ERROR: {exc}")
        return 1

    path = _write(fetch, "the_odds_api_odds.json", fixture_dir)
    print(f"  OK  {_summarize(fetch.payload)} -> {path.name}")
    if fetch.quota_remaining is not None:
        print(f"  Cuota restante: {fetch.quota_remaining}")
    return 0


def run_capture(fixture_dir: Path | None = None) -> int:
    target = fixture_dir or FIXTURE_DIR
    print("=" * 62)
    print("  CAPTURA DE FIXTURES")
    print("=" * 62)
    print(f"\n  Destino: {target}")

    failures = capture_mlb_schedule(target)

    api_key = os.environ.get(ODDS_API_KEY_ENV, "").strip()
    if not api_key:
        print(f"\n[the-odds-api] omitido: {ODDS_API_KEY_ENV} no está definida.")
        print("  export SPORTSTAR_ODDS_API_KEY=... y vuelve a ejecutar.")
    else:
        failures += capture_odds(target, api_key)

    print("\n" + "-" * 62)
    if failures:
        print(f"  {failures} proveedor(es) fallaron. Los fixtures previos siguen en su sitio.")
    else:
        print("  Fixtures actualizados. Ahora:  pytest tests/data -q")
    return 1 if failures else 0
