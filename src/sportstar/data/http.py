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
import re
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
    """Fallo de red o respuesta no exitosa tras agotar los reintentos.

    Conserva el **cuerpo** de la respuesta de error, no solo el código. Los
    proveedores explican en el cuerpo qué pasó exactamente, y perder ese texto
    convierte un diagnóstico de diez segundos en media hora de conjeturas:

        HTTP 401: Unauthorized

    frente a lo que de verdad manda The Odds API:

        {"message": "API key is not valid. Get an API key at ...",
         "error_code": "INVALID_KEY"}
    """

    def __init__(
        self, message: str, *, status: int | None = None, url: str = "", body: str = ""
    ) -> None:
        super().__init__(message)
        self.status = status
        self.url = url
        self.body = body

    @property
    def provider_message(self) -> str | None:
        """El campo `message` del cuerpo, si el proveedor manda JSON."""
        if not self.body:
            return None
        try:
            payload = json.loads(self.body)
        except json.JSONDecodeError:
            return self.body[:200]
        if isinstance(payload, dict):
            for key in ("message", "error", "detail"):
                value = payload.get(key)
                if isinstance(value, str):
                    return value
        return None

    @property
    def provider_error_code(self) -> str | None:
        """Código de error del proveedor, útil para distinguir causas.

        The Odds API usa `INVALID_KEY`, `OUT_OF_USAGE_CREDITS`,
        `UNKNOWN_SPORT`... Distinguirlas importa: una es un typo y otra es que se
        acabó la cuota, y solo una se arregla volviendo a intentarlo mañana.
        """
        if not self.body:
            return None
        try:
            payload = json.loads(self.body)
        except json.JSONDecodeError:
            return None
        code = payload.get("error_code") if isinstance(payload, dict) else None
        return code if isinstance(code, str) else None


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
                body = _read_error_body(exc)
                if exc.code not in RETRYABLE_STATUS:
                    # 401, 403, 404, 422: reintentar no lo arregla y gasta cuota.
                    error = HttpError(
                        f"HTTP {exc.code} en {_redact(full_url)}: {exc.reason}",
                        status=exc.code,
                        url=_redact(full_url),
                        body=body,
                    )
                    if error.provider_message:
                        error = HttpError(
                            f"HTTP {exc.code}: {error.provider_message}",
                            status=exc.code,
                            url=_redact(full_url),
                            body=body,
                        )
                    raise error from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = exc

            if attempt < self.retries:
                self.sleep(self.backoff_base ** (attempt - 1))

        raise HttpError(
            f"agotados {self.retries} intentos contra {_redact(full_url)}: {last_error}",
            url=_redact(full_url),
        )


def _read_error_body(exc: urllib.error.HTTPError) -> str:
    """Lee el cuerpo del error. Nunca propaga un fallo de lectura.

    Si el cuerpo no se puede leer, el error original sigue siendo lo importante:
    perderlo por una excepción secundaria al diagnosticar sería absurdo.
    """
    try:
        return exc.read().decode("utf-8", errors="replace")[:2000]
    except Exception:
        return ""


def _redact(url: str) -> str:
    """Oculta la API key de la URL.

    Los mensajes de error acaban en logs, en `job_runs.error_summary` y en la
    pantalla. Una key que viaja en la query string no puede filtrarse por ahí.
    """
    return re.sub(r"([?&](?:apiKey|api_key|key)=)[^&]*", r"\1***", url, flags=re.IGNORECASE)
