"""Cliente HTTP mínimo para proveedores de datos.

Usa `urllib` de la stdlib en lugar de añadir una dependencia: lo único que
necesitamos son GETs con timeout y reintentos, y eso son cuarenta líneas. Respeta
`HTTP_PROXY`/`HTTPS_PROXY` del entorno automáticamente, que es como sale el
tráfico en entornos gestionados.

Los reintentos solo cubren fallos **transitorios** (5xx, 429, errores de red).
Un 401 o un 404 no se reintentan: repetir una petición mal formada no la arregla,
solo gasta cuota — y en The Odds API la cuota es dinero.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

DEFAULT_TIMEOUT = 20.0
DEFAULT_RETRIES = 3
RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504})
USER_AGENT = "sportstar/0.1 (+https://github.com/dpineyro99/sportstar)"


class HttpError(RuntimeError):
    """Fallo de red o respuesta no exitosa tras agotar los reintentos."""

    def __init__(self, message: str, *, status: int | None = None, url: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.url = url


@dataclass(frozen=True, slots=True)
class HttpResponse:
    """Respuesta cruda. `observed_at` es lo que ancla el contrato point-in-time.

    No es la fecha del hecho, es cuándo el hecho estuvo disponible **para
    nosotros**. Es lo que el backtest necesita para no usar información que en su
    momento no teníamos.
    """

    url: str
    status: int
    body: str
    headers: dict[str, str]
    requested_at: datetime
    observed_at: datetime
    attempts: int = 1

    def json(self) -> Any:
        return json.loads(self.body)

    @property
    def quota_remaining(self) -> int | None:
        """Peticiones restantes según el proveedor, si lo indica.

        The Odds API lo devuelve en `x-requests-remaining`. Quedarse sin cuota a
        mitad de temporada sin haberlo visto venir es una forma tonta de perder
        el histórico de un día.
        """
        raw = self.headers.get("x-requests-remaining")
        if raw is None:
            return None
        try:
            return int(raw)
        except ValueError:
            return None


@dataclass
class HttpClient:
    """GETs con reintentos y backoff exponencial."""

    timeout: float = DEFAULT_TIMEOUT
    retries: int = DEFAULT_RETRIES
    backoff_base: float = 2.0
    user_agent: str = USER_AGENT
    # Inyectable para poder testear el backoff sin esperas reales.
    sleep: Any = field(default=time.sleep)

    def get(self, url: str, params: dict[str, Any] | None = None) -> HttpResponse:
        full_url = url
        if params:
            query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
            full_url = f"{url}{'&' if '?' in url else '?'}{query}"

        requested_at = datetime.now(UTC)
        last_error: Exception | None = None

        for attempt in range(1, self.retries + 1):
            try:
                request = urllib.request.Request(
                    full_url, headers={"User-Agent": self.user_agent, "Accept": "application/json"}
                )
                if not full_url.startswith("https://"):
                    raise HttpError(f"solo se permiten URLs https: {full_url}", url=full_url)

                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    body = response.read().decode("utf-8")
                    return HttpResponse(
                        url=full_url,
                        status=response.status,
                        body=body,
                        headers={k.lower(): v for k, v in response.headers.items()},
                        requested_at=requested_at,
                        observed_at=datetime.now(UTC),
                        attempts=attempt,
                    )
            except urllib.error.HTTPError as exc:
                last_error = exc
                if exc.code not in RETRYABLE_STATUS:
                    # 401, 403, 404, 422: reintentar no lo arregla y gasta cuota.
                    raise HttpError(
                        f"HTTP {exc.code} en {full_url}: {exc.reason}",
                        status=exc.code,
                        url=full_url,
                    ) from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = exc

            if attempt < self.retries:
                self.sleep(self.backoff_base ** (attempt - 1))

        raise HttpError(
            f"agotados {self.retries} intentos contra {full_url}: {last_error}", url=full_url
        )
