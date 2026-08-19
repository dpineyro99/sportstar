"""Contrato de los proveedores de datos.

Un provider **solo trae bytes**. No interpreta, no empareja, no toca la base.
Toda la interpretación vive en `data/normalizers/`, y el emparejamiento con el
catálogo en `resolution/`.

La separación no es purismo: permite guardar el payload íntegro en `raw_payloads`
y reprocesar todo el histórico cuando un normalizador tenga un bug, sin volver a
pagar la API.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from ..http import HttpResponse


@dataclass(frozen=True, slots=True)
class RawFetch:
    """Lo que devuelve un provider: payload íntegro más su procedencia."""

    provider: str
    endpoint: str
    sport_key: str | None
    payload: Any
    requested_at: datetime
    observed_at: datetime
    http_status: int
    quota_remaining: int | None = None

    @classmethod
    def from_response(
        cls, response: HttpResponse, *, provider: str, endpoint: str, sport_key: str | None
    ) -> RawFetch:
        return cls(
            provider=provider,
            endpoint=endpoint,
            sport_key=sport_key,
            payload=response.json(),
            requested_at=response.requested_at,
            observed_at=response.observed_at,
            http_status=response.status,
            quota_remaining=response.quota_remaining,
        )


class DataProvider(Protocol):
    """Todo provider se identifica para poder trazar de dónde salió un dato."""

    provider_key: str
