"""Normalizador de The Odds API v4.

Convierte el payload en `NormalizedEvent` + `NormalizedPrice`. No empareja nada
con el catálogo: eso es trabajo de `resolution/`.

Forma esperada (v4, `/sports/{sport}/odds`):

    [
      {
        "id": "...", "sport_key": "baseball_mlb",
        "commence_time": "2026-08-19T23:05:00Z",
        "home_team": "New York Yankees", "away_team": "Boston Red Sox",
        "bookmakers": [
          {"key": "pinnacle", "title": "Pinnacle", "last_update": "...",
           "markets": [
             {"key": "h2h", "outcomes": [
                {"name": "New York Yankees", "price": -120},
                {"name": "Boston Red Sox",   "price": 105}]}]}
        ]
      }
    ]

En `spreads` y `totals` cada outcome trae además `point`. En `totals` el `name`
es "Over"/"Under" en vez de un nombre de equipo.
"""

from __future__ import annotations

from datetime import datetime

from ..providers.the_odds_api import MARKET_KEYS, PROVIDER_KEY
from .errors import (
    ShapeError,
    optional_number,
    require_dict,
    require_list,
    require_number,
    require_str,
)
from .models import NormalizationResult, NormalizedEvent, NormalizedPrice

GAME_PERIOD = "game"


def parse_iso8601(value: str, *, path: str) -> datetime:
    """ISO-8601 con `Z`, que `fromisoformat` no acepta antes de 3.11 en todos los casos."""
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ShapeError(f"{path}: fecha ISO-8601 inválida: {value!r}") from exc


def normalize_odds(
    payload: object,
    *,
    sport_key: str,
    allowed_book_keys: set[str] | None = None,
) -> NormalizationResult:
    """Normaliza una respuesta de odds.

    `allowed_book_keys` filtra a los books que el catálogo conoce. Los demás se
    registran en `skipped_books` en vez de descartarse en silencio: un book nuevo
    en el feed es información —puede ser un sharp que deberíamos estar usando— y
    enterarse tres meses después es tarde.
    """
    result = NormalizationResult()

    events = require_list(payload, path="payload")
    for index, raw_event in enumerate(events):
        path = f"payload[{index}]"
        try:
            event = require_dict(raw_event, path=path)
            normalized = NormalizedEvent(
                provider=PROVIDER_KEY,
                provider_event_id=require_str(event, "id", path=path),
                sport_key=sport_key,
                start_time=parse_iso8601(
                    require_str(event, "commence_time", path=path),
                    path=f"{path}.commence_time",
                ),
                home_team_raw=require_str(event, "home_team", path=path),
                away_team_raw=require_str(event, "away_team", path=path),
            )
            result.events.append(normalized)
            result.prices.extend(
                _normalize_bookmakers(
                    event, normalized, result, path=path, allowed=allowed_book_keys
                )
            )
        except ShapeError as exc:
            # Un evento con forma rara no tira el slate entero, pero queda contado.
            result.errors.append(str(exc))

    return result


def _normalize_bookmakers(
    event: dict[str, object],
    normalized: NormalizedEvent,
    result: NormalizationResult,
    *,
    path: str,
    allowed: set[str] | None,
) -> list[NormalizedPrice]:
    prices: list[NormalizedPrice] = []

    # Un evento sin `bookmakers` es normal (aún no hay precios), no un error de
    # forma: solo se convierte en problema si NINGÚN evento trae precios, y eso
    # lo detecta la regla `matched == 0` del JobReport.
    bookmakers = require_list(event.get("bookmakers", []), path=f"{path}.bookmakers")

    for b_index, raw_book in enumerate(bookmakers):
        b_path = f"{path}.bookmakers[{b_index}]"
        book = require_dict(raw_book, path=b_path)
        book_key = require_str(book, "key", path=b_path)

        if allowed is not None and book_key not in allowed:
            result.skipped_books.add(book_key)
            continue

        last_update = (
            parse_iso8601(book["last_update"], path=f"{b_path}.last_update")
            if isinstance(book.get("last_update"), str)
            else None
        )

        for m_index, raw_market in enumerate(
            require_list(book.get("markets", []), path=f"{b_path}.markets")
        ):
            m_path = f"{b_path}.markets[{m_index}]"
            market = require_dict(raw_market, path=m_path)
            provider_market = require_str(market, "key", path=m_path)
            market_type = MARKET_KEYS.get(provider_market)
            if market_type is None:
                # Mercado que todavía no modelamos (props, alternates). No es un
                # error: la taxonomía ya los contempla, simplemente no los
                # pedimos aún.
                continue

            for o_index, raw_outcome in enumerate(
                require_list(market.get("outcomes", []), path=f"{m_path}.outcomes")
            ):
                o_path = f"{m_path}.outcomes[{o_index}]"
                outcome = require_dict(raw_outcome, path=o_path)
                prices.append(
                    NormalizedPrice(
                        provider=PROVIDER_KEY,
                        provider_event_id=normalized.provider_event_id,
                        book_key=book_key,
                        market_type=market_type,
                        period=GAME_PERIOD,
                        side_raw=require_str(outcome, "name", path=o_path),
                        price_american=require_number(outcome, "price", path=o_path),
                        line=optional_number(outcome, "point", path=o_path),
                        last_update=last_update,
                    )
                )

    return prices
