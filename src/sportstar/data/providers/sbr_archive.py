"""Proveedor de odds históricas: archivo de Sportsbook Review (vía GitHub).

Qué es
------
`flancast90/sportsbookreview-scraper` publica, bajo licencia MIT, un volcado
pre-scrapeado de los archivos históricos de sportsbookreviewsonline.com: once
temporadas de MLB (2011-2021) con **moneyline de apertura y de cierre** para
ambos lados, más totales y runline. Es lo que faltaba para poder backtestear:
The Odds API solo sirve histórico en su plan de pago.

Por qué se pincha a un commit
-----------------------------
Se descarga por HTTPS desde `raw.githubusercontent.com` anclado a un SHA, no a
una rama. Un backtest cuyo dataset de entrada puede cambiar bajo los pies no es
reproducible, y un cambio silencioso en el upstream se manifestaría como una
"mejora" del modelo que en realidad es otro dataset.

Advertencia importante sobre estos datos
----------------------------------------
El volcado publicado **está corrompido por un bug del scraper original** que
empareja mal las filas: cada partido publicado mezcla dos partidos distintos.
El bug es determinista y reversible, y `normalizers/sbr_archive.py` lo detecta y
lo corrige. Este módulo solo trae bytes; no interpreta nada.

Procedencia y licencia
----------------------
El código del repositorio de origen es MIT (© 2023 Finn Lancaster). Los datos
subyacentes son de sportsbookreviewsonline.com y no llevan licencia explícita:
se usan aquí para investigación propia, y **no se redistribuyen** desde este
repositorio —de ahí que se descarguen en tiempo de ejecución y se cacheen en
local en vez de vendorizarse.

Ninguno de estos precios lleva marca de tiempo ni identifica la casa. Son la
línea de consenso que publica SBR, con dos observaciones por partido: apertura y
cierre. Eso permite medir CLV y backtestear a cierre; **no** permite reconstruir
el movimiento de línea intradía ni comparar entre casas.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..http import HttpClient
from .base import RawFetch

PROVIDER_KEY = "sbr-archive"

# Anclado a un commit, nunca a una rama. Ver docstring del módulo.
UPSTREAM_REPO = "flancast90/sportsbookreview-scraper"
UPSTREAM_COMMIT = "1a820e50c6fbd0276cde073c22bfffae78930868"
BASE_URL = f"https://raw.githubusercontent.com/{UPSTREAM_REPO}/{UPSTREAM_COMMIT}"

# Deportes disponibles en el volcado -> nombre del fichero.
SPORT_FILES: dict[str, str] = {
    "mlb": "data/mlb_archive_10Y.json",
    "nba": "data/nba_archive_10Y.json",
    "nfl": "data/nfl_archive_10Y.json",
    "nhl": "data/nhl_archive_10Y.json",
}

# Cobertura declarada por el upstream. Verificada para MLB: 2011-2021, con 2020
# corto (949 partidos) por la temporada acortada, que es justo lo que debe salir.
COVERED_SEASONS = range(2011, 2022)


class SbrArchiveProvider:
    """Descarga el volcado histórico. No lo interpreta ni lo corrige."""

    provider_key = PROVIDER_KEY

    def __init__(self, client: HttpClient | None = None, *, cache_dir: Path | None = None) -> None:
        self._client = client or HttpClient()
        self._cache_dir = cache_dir

    def fetch(self, sport: str) -> RawFetch:
        """Trae el volcado completo de un deporte.

        Son entre 1 y 18 MB en un solo GET. Se cachea en disco si se configuró un
        `cache_dir`: no hay cuota que gastar, pero sí hay ancho de banda y
        paciencia, y el fichero está pinchado a un commit, así que la caché nunca
        puede quedar obsoleta respecto a lo que se pidió.
        """
        try:
            path = SPORT_FILES[sport]
        except KeyError:
            raise ValueError(
                f"deporte {sport!r} no está en el volcado. Disponibles: "
                f"{', '.join(sorted(SPORT_FILES))}"
            ) from None

        cached = self._read_cache(sport)
        if cached is not None:
            return cached

        response = self._client.get(f"{BASE_URL}/{path}")
        fetch = RawFetch.from_response(
            response, provider=PROVIDER_KEY, endpoint=path, sport_key=sport
        )
        self._write_cache(sport, response.body)
        return fetch

    def _cache_path(self, sport: str) -> Path | None:
        if self._cache_dir is None:
            return None
        # El commit va en el nombre: pedir otro commit no puede leer esta caché.
        return self._cache_dir / f"{PROVIDER_KEY}_{sport}_{UPSTREAM_COMMIT[:12]}.json"

    def _read_cache(self, sport: str) -> RawFetch | None:
        path = self._cache_path(sport)
        if path is None or not path.exists():
            return None
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
        from datetime import UTC, datetime

        # `observed_at` es cuándo se descargó, no cuándo se lee la caché. Se
        # aproxima con la mtime del fichero, que es lo más cercano que queda.
        observed = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
        return RawFetch(
            provider=PROVIDER_KEY,
            endpoint=SPORT_FILES[sport],
            sport_key=sport,
            payload=payload,
            requested_at=observed,
            observed_at=observed,
            http_status=200,
        )

    def _write_cache(self, sport: str, body: str) -> None:
        path = self._cache_path(sport)
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
