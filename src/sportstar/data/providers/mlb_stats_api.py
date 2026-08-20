"""Proveedor de calendario y resultados: MLB Stats API (oficial, gratuita).

⚠️ **Sin verificar contra respuestas reales** desde este entorno. Ver la nota en
`the_odds_api.py`; aplica igual.

Es la mejor fuente disponible para MLB: oficial, gratuita, sin API key, con
histórico y con granularidad hasta el lanzamiento. Es una de las razones por las
que MLB es buen sitio para construir la infraestructura aunque sea mal sitio para
buscar edge de modelo.
"""

from __future__ import annotations

from datetime import date

from ..http import HttpClient
from .base import RawFetch

BASE_URL = "https://statsapi.mlb.com/api/v1"
PROVIDER_KEY = "mlb-stats-api"
MLB_SPORT_ID = 1

# `hydrate` evita una petición por partido para traer pitcher probable y marcador.
SCHEDULE_HYDRATE = "probablePitcher,linescore,team"


class MlbStatsApiProvider:
    """Trae calendario y resultados. No los interpreta."""

    provider_key = PROVIDER_KEY

    def __init__(self, client: HttpClient | None = None) -> None:
        self._client = client or HttpClient()

    def fetch_schedule(self, target: date) -> RawFetch:
        """Partidos de un día, con pitcher probable y marcador si existe."""
        response = self._client.get(
            f"{BASE_URL}/schedule",
            params={
                "sportId": MLB_SPORT_ID,
                "date": target.isoformat(),
                "hydrate": SCHEDULE_HYDRATE,
            },
        )
        return RawFetch.from_response(
            response, provider=self.provider_key, endpoint="/schedule", sport_key="mlb"
        )

    def fetch_schedule_range(self, start: date, end: date) -> RawFetch:
        """Calendario de un rango de fechas en **una sola petición**.

        Es lo que hace viable el backfill: una temporada completa son ~2.430
        partidos, y pedirlos día a día serían 180 peticiones. Por meses son seis.
        """
        response = self._client.get(
            f"{BASE_URL}/schedule",
            params={
                "sportId": MLB_SPORT_ID,
                "startDate": start.isoformat(),
                "endDate": end.isoformat(),
                "hydrate": SCHEDULE_HYDRATE,
            },
        )
        return RawFetch.from_response(
            response, provider=self.provider_key, endpoint="/schedule", sport_key="mlb"
        )

    def fetch_teams(self) -> RawFetch:
        """Equipos de MLB. Se usa para sembrar `external_ids` y cerrar el
        emparejamiento por ID en vez de por nombre."""
        response = self._client.get(f"{BASE_URL}/teams", params={"sportId": MLB_SPORT_ID})
        return RawFetch.from_response(
            response, provider=self.provider_key, endpoint="/teams", sport_key="mlb"
        )
