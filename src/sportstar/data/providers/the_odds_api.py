"""Proveedor de odds: The Odds API v4.

⚠️ **Sin verificar contra respuestas reales.** El esquema está escrito según la
documentación pública de la v4; no se ha podido ejecutar contra el servicio desde
el entorno de desarrollo. La primera ejecución real es una verificación, y los
normalizadores están escritos para fallar con un diagnóstico preciso —qué clave
faltaba, qué claves llegaron— en vez de devolver cero resultados en silencio.

Cuota: el plan gratuito son 500 peticiones al mes. Cada petición de odds cuesta
`1 x nº de mercados x nº de regiones`, así que pedir `h2h,spreads,totals` en `us`
son 3 créditos. A un sync cada 10 minutos durante una jornada de MLB eso se agota
en días. La estrategia de frecuencia hay que decidirla con la cuota delante.
"""

from __future__ import annotations

from ..http import HttpClient
from .base import RawFetch

BASE_URL = "https://api.the-odds-api.com/v4"
PROVIDER_KEY = "the-odds-api"

# Claves de deporte del proveedor -> claves internas del catálogo.
SPORT_KEYS: dict[str, str] = {
    "mlb": "baseball_mlb",
    "nba": "basketball_nba",
    "nfl": "americanfootball_nfl",
    "nhl": "icehockey_nhl",
    "ncaab": "basketball_ncaab",
    "ncaaf": "americanfootball_ncaaf",
}

# Mercados del proveedor -> taxonomía interna (`db.enums.MarketType`).
MARKET_KEYS: dict[str, str] = {
    "h2h": "moneyline",
    "spreads": "spread",
    "totals": "total",
}

DEFAULT_MARKETS = ("h2h",)
DEFAULT_REGIONS = ("us",)


class TheOddsApiProvider:
    """Trae odds. No las interpreta."""

    provider_key = PROVIDER_KEY

    def __init__(self, api_key: str, client: HttpClient | None = None) -> None:
        if not api_key:
            raise ValueError("falta la API key de The Odds API. Se lee de SPORTSTAR_ODDS_API_KEY.")
        self._api_key = api_key
        self._client = client or HttpClient()

    def fetch_odds(
        self,
        sport: str,
        *,
        markets: tuple[str, ...] = DEFAULT_MARKETS,
        regions: tuple[str, ...] = DEFAULT_REGIONS,
    ) -> RawFetch:
        """Odds de todos los eventos próximos de un deporte.

        `oddsFormat=american` porque es el formato nativo del mercado
        estadounidense y evita una conversión con pérdida de precisión en origen:
        preferimos convertir nosotros, con funciones que están testeadas.
        """
        provider_sport = SPORT_KEYS.get(sport, sport)
        endpoint = f"/sports/{provider_sport}/odds"
        response = self._client.get(
            f"{BASE_URL}{endpoint}",
            params={
                "apiKey": self._api_key,
                "regions": ",".join(regions),
                "markets": ",".join(markets),
                "oddsFormat": "american",
                "dateFormat": "iso",
            },
        )
        return RawFetch.from_response(
            response, provider=self.provider_key, endpoint=endpoint, sport_key=sport
        )

    def fetch_sports(self) -> RawFetch:
        """Catálogo de deportes. Útil para verificar la key sin gastar cuota de odds."""
        response = self._client.get(f"{BASE_URL}/sports", params={"apiKey": self._api_key})
        return RawFetch.from_response(
            response, provider=self.provider_key, endpoint="/sports", sport_key=None
        )
