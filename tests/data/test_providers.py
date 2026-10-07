"""Providers.

Un provider solo trae bytes. Estos tests verifican que construye bien la petición
y que no interpreta nada: la interpretación es de los normalizadores, y la
separación es lo que permite reprocesar el histórico sin volver a pagar la API.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from sportstar.data.http import HttpClient, HttpResponse
from sportstar.data.providers import MlbStatsApiProvider, TheOddsApiProvider
from sportstar.data.providers.the_odds_api import MARKET_KEYS, SPORT_KEYS


class RecordingClient(HttpClient):
    """Registra la URL en vez de salir a la red."""

    def __init__(self) -> None:
        super().__init__()
        self.urls: list[str] = []
        self.params: list[dict[str, Any]] = []

    def get(self, url: str, params: dict[str, Any] | None = None) -> HttpResponse:
        from datetime import UTC, datetime

        self.urls.append(url)
        self.params.append(params or {})
        now = datetime.now(UTC)
        return HttpResponse(
            url=url,
            status=200,
            body="[]",
            headers={"x-requests-remaining": "480"},
            requested_at=now,
            observed_at=now,
        )


class TestTheOddsApi:
    def test_requires_an_api_key(self) -> None:
        with pytest.raises(ValueError, match="SPORTSTAR_ODDS_API_KEY"):
            TheOddsApiProvider("")

    def test_maps_internal_sport_keys_to_provider_keys(self) -> None:
        client = RecordingClient()
        TheOddsApiProvider("k", client).fetch_odds("mlb")
        assert "baseball_mlb" in client.urls[0]

    def test_every_priority_sport_has_a_provider_key(self) -> None:
        # Los seis de la fase inicial del roadmap.
        assert {"mlb", "nba", "nfl", "nhl", "ncaab", "ncaaf"} <= set(SPORT_KEYS)

    def test_market_keys_map_to_the_internal_taxonomy(self) -> None:
        from sportstar.db.enums import MarketType

        assert set(MARKET_KEYS.values()) <= {m.value for m in MarketType}

    def test_requests_american_odds(self) -> None:
        # Formato nativo del mercado: convertimos nosotros, con funciones testeadas,
        # en vez de aceptar una conversión con pérdida en origen.
        client = RecordingClient()
        TheOddsApiProvider("k", client).fetch_odds("mlb")
        assert client.params[0]["oddsFormat"] == "american"

    def test_passes_markets_and_regions(self) -> None:
        client = RecordingClient()
        TheOddsApiProvider("k", client).fetch_odds(
            "mlb", markets=("h2h", "totals"), regions=("us", "eu")
        )
        assert client.params[0]["markets"] == "h2h,totals"
        assert client.params[0]["regions"] == "us,eu"

    def test_fetch_returns_raw_payload_with_provenance(self) -> None:
        fetch = TheOddsApiProvider("k", RecordingClient()).fetch_odds("mlb")
        assert fetch.provider == "the-odds-api"
        assert fetch.sport_key == "mlb"  # clave interna, no la del proveedor
        assert fetch.payload == []
        assert fetch.quota_remaining == 480
        assert fetch.observed_at >= fetch.requested_at

    def test_sports_endpoint_does_not_need_a_sport(self) -> None:
        fetch = TheOddsApiProvider("k", RecordingClient()).fetch_sports()
        assert fetch.sport_key is None
        assert fetch.endpoint == "/sports"


class TestMlbStatsApi:
    def test_needs_no_api_key(self) -> None:
        # Oficial y gratuita: una de las razones por las que MLB es buen sitio
        # para construir infraestructura.
        assert MlbStatsApiProvider(RecordingClient()).provider_key == "mlb-stats-api"

    def test_requests_a_specific_date(self) -> None:
        client = RecordingClient()
        MlbStatsApiProvider(client).fetch_schedule(date(2026, 8, 19))
        assert client.params[0]["date"] == "2026-08-19"
        assert client.params[0]["sportId"] == 1

    def test_hydrates_probable_pitcher_in_the_same_request(self) -> None:
        # Sin `hydrate` haría falta una petición por partido para el pitcher
        # probable, que es la feature más importante de MLB.
        client = RecordingClient()
        MlbStatsApiProvider(client).fetch_schedule(date(2026, 8, 19))
        assert "probablePitcher" in client.params[0]["hydrate"]

    def test_teams_endpoint_targets_mlb(self) -> None:
        client = RecordingClient()
        fetch = MlbStatsApiProvider(client).fetch_teams()
        assert client.params[0]["sportId"] == 1
        assert fetch.endpoint == "/teams"
