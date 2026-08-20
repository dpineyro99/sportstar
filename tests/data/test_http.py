"""Cliente HTTP.

Lo que se verifica aquí no es que sepa hacer un GET, sino que **no gaste cuota
reintentando lo que no se arregla reintentando**. En The Odds API la cuota es
dinero y agotarla a mitad de temporada pierde histórico irrecuperable.
"""

from __future__ import annotations

import io
import urllib.error
from typing import Any

import pytest

from sportstar.data.http import HttpClient, HttpError


class FakeResponse:
    def __init__(self, body: str, status: int = 200, headers: dict[str, str] | None = None) -> None:
        self._body = body.encode()
        self.status = status
        self.headers = headers or {}

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None


def patch_urlopen(monkeypatch: pytest.MonkeyPatch, behaviours: list[Any]) -> list[str]:
    """Sustituye urlopen por una secuencia de respuestas o excepciones."""
    calls: list[str] = []

    def fake_urlopen(request: Any, timeout: float = 0) -> Any:
        calls.append(request.full_url)
        outcome = behaviours[min(len(calls) - 1, len(behaviours) - 1)]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    return calls


def http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://x/", code, "boom", {}, io.BytesIO(b""))  # type: ignore[arg-type]


def client(**kwargs: Any) -> HttpClient:
    kwargs.setdefault("sleep", lambda _: None)  # sin esperas reales en test
    return HttpClient(**kwargs)


class TestSuccess:
    def test_returns_the_parsed_body(self, monkeypatch: pytest.MonkeyPatch) -> None:
        patch_urlopen(monkeypatch, [FakeResponse('{"ok": true}')])
        response = client().get("https://example.com/api")
        assert response.json() == {"ok": True}
        assert response.status == 200
        assert response.attempts == 1

    def test_encodes_query_parameters(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = patch_urlopen(monkeypatch, [FakeResponse("{}")])
        client().get("https://example.com/api", params={"a": 1, "b": "x,y"})
        assert "a=1" in calls[0] and "b=x%2Cy" in calls[0]

    def test_omits_none_parameters(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = patch_urlopen(monkeypatch, [FakeResponse("{}")])
        client().get("https://example.com/api", params={"a": 1, "b": None})
        assert "b=" not in calls[0]

    def test_records_when_the_data_became_available_to_us(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # `observed_at` es lo que ancla el contrato point-in-time del backtest.
        patch_urlopen(monkeypatch, [FakeResponse("{}")])
        response = client().get("https://example.com/api")
        assert response.observed_at >= response.requested_at
        assert response.observed_at.tzinfo is not None


class TestQuota:
    def test_reads_the_remaining_quota_header(self, monkeypatch: pytest.MonkeyPatch) -> None:
        patch_urlopen(monkeypatch, [FakeResponse("{}", headers={"x-requests-remaining": "412"})])
        assert client().get("https://example.com/api").quota_remaining == 412

    def test_absent_header_reads_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        patch_urlopen(monkeypatch, [FakeResponse("{}")])
        assert client().get("https://example.com/api").quota_remaining is None

    def test_unparseable_header_reads_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        patch_urlopen(monkeypatch, [FakeResponse("{}", headers={"x-requests-remaining": "?"})])
        assert client().get("https://example.com/api").quota_remaining is None


class TestRetries:
    def test_retries_transient_failures(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = patch_urlopen(monkeypatch, [http_error(503), FakeResponse('{"ok": 1}')])
        response = client(retries=3).get("https://example.com/api")
        assert response.attempts == 2
        assert len(calls) == 2

    def test_retries_network_errors(self, monkeypatch: pytest.MonkeyPatch) -> None:
        patch_urlopen(monkeypatch, [urllib.error.URLError("sin ruta"), FakeResponse("{}")])
        assert client(retries=3).get("https://example.com/api").attempts == 2

    @pytest.mark.parametrize("code", [400, 401, 403, 404, 422])
    def test_does_not_retry_what_retrying_cannot_fix(
        self, monkeypatch: pytest.MonkeyPatch, code: int
    ) -> None:
        """Una key mal puesta o una URL equivocada no se arregla insistiendo.

        Reintentar solo gasta cuota, y en The Odds API la cuota es dinero.
        """
        calls = patch_urlopen(monkeypatch, [http_error(code)])
        with pytest.raises(HttpError) as exc:
            client(retries=3).get("https://example.com/api")
        assert len(calls) == 1
        assert exc.value.status == code

    @pytest.mark.parametrize("code", [429, 500, 502, 503, 504])
    def test_retries_the_codes_worth_retrying(
        self, monkeypatch: pytest.MonkeyPatch, code: int
    ) -> None:
        calls = patch_urlopen(monkeypatch, [http_error(code)])
        with pytest.raises(HttpError):
            client(retries=3).get("https://example.com/api")
        assert len(calls) == 3

    def test_gives_up_with_a_clear_message(self, monkeypatch: pytest.MonkeyPatch) -> None:
        patch_urlopen(monkeypatch, [http_error(503)])
        with pytest.raises(HttpError, match="agotados 3 intentos"):
            client(retries=3).get("https://example.com/api")

    def test_backoff_grows_between_attempts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        waits: list[float] = []
        patch_urlopen(monkeypatch, [http_error(503)])
        with pytest.raises(HttpError):
            HttpClient(retries=4, sleep=waits.append).get("https://example.com/api")
        assert waits == sorted(waits) and waits[0] < waits[-1]


class TestSecurity:
    def test_refuses_plain_http(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Las API keys viajan en la query string: sin TLS irían en claro.
        patch_urlopen(monkeypatch, [FakeResponse("{}")])
        with pytest.raises(HttpError, match="solo se permiten URLs https"):
            client().get("http://example.com/api")


class TestErrorDiagnostics:
    """El cuerpo del error del proveedor se conserva y se expone.

    "HTTP 401: Unauthorized" convierte un diagnóstico de diez segundos en media
    hora de conjeturas. El proveedor ya dice exactamente qué pasó; tirar ese texto
    es desperdiciar la única información útil que manda.
    """

    INVALID_KEY_BODY = (
        '{"message":"API key is not valid. Get an API key at https://the-odds-api.com",'
        '"error_code":"INVALID_KEY",'
        '"details_url":"https://the-odds-api.com/liveapi/guides/v4/api-error-codes.html"}'
    )

    def error_with_body(self, code: int, body: str) -> urllib.error.HTTPError:
        return urllib.error.HTTPError(
            "https://x/", code, "Unauthorized", {}, io.BytesIO(body.encode())
        )  # type: ignore[arg-type]

    def test_surfaces_the_provider_message_instead_of_the_status_phrase(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Respuesta real de The Odds API ante una key inválida."""
        patch_urlopen(monkeypatch, [self.error_with_body(401, self.INVALID_KEY_BODY)])
        with pytest.raises(HttpError) as exc:
            client().get("https://example.com/api")
        assert "API key is not valid" in str(exc.value)
        assert exc.value.provider_error_code == "INVALID_KEY"

    def test_error_code_distinguishes_causes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Una key mal escrita y quedarse sin cuota son problemas distintos.

        Solo uno de los dos se arregla esperando a mañana, así que el pipeline
        necesita poder distinguirlos sin leer prosa.
        """
        body = '{"message":"Usage quota has been reached","error_code":"OUT_OF_USAGE_CREDITS"}'
        patch_urlopen(monkeypatch, [self.error_with_body(401, body)])
        with pytest.raises(HttpError) as exc:
            client().get("https://example.com/api")
        assert exc.value.provider_error_code == "OUT_OF_USAGE_CREDITS"

    def test_non_json_body_is_truncated_not_dropped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        patch_urlopen(monkeypatch, [self.error_with_body(500, "<html>Gateway error</html>")])
        with pytest.raises(HttpError) as exc:
            client(retries=1).get("https://example.com/api")
        assert exc.value.status is None or "Gateway" in (exc.value.provider_message or "")

    def test_an_unreadable_body_does_not_mask_the_original_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Perder el error de origen por una excepción secundaria al diagnosticar
        # sería absurdo.
        broken = urllib.error.HTTPError("https://x/", 401, "Unauthorized", {}, None)  # type: ignore[arg-type]
        patch_urlopen(monkeypatch, [broken])
        with pytest.raises(HttpError) as exc:
            client().get("https://example.com/api")
        assert exc.value.status == 401


class TestKeyRedaction:
    """La API key viaja en la query string y los errores acaban en logs.

    Un mensaje de error se escribe en `job_runs.error_summary`, en el log del
    worker y en pantalla. Si la key va dentro, se filtra por tres sitios a la vez.
    """

    def test_redacts_the_key_in_error_messages(self, monkeypatch: pytest.MonkeyPatch) -> None:
        patch_urlopen(monkeypatch, [http_error(401)])
        with pytest.raises(HttpError) as exc:
            client().get("https://example.com/api", params={"apiKey": "secreto123"})
        assert "secreto123" not in str(exc.value)
        assert "secreto123" not in exc.value.url

    def test_redacts_after_exhausting_retries(self, monkeypatch: pytest.MonkeyPatch) -> None:
        patch_urlopen(monkeypatch, [http_error(503)])
        with pytest.raises(HttpError) as exc:
            client(retries=2).get("https://example.com/api", params={"apiKey": "secreto123"})
        assert "secreto123" not in str(exc.value)

    @pytest.mark.parametrize("param", ["apiKey", "api_key", "key", "APIKEY"])
    def test_redacts_common_key_parameter_names(
        self, monkeypatch: pytest.MonkeyPatch, param: str
    ) -> None:
        patch_urlopen(monkeypatch, [http_error(401)])
        with pytest.raises(HttpError) as exc:
            client().get("https://example.com/api", params={param: "secreto123"})
        assert "secreto123" not in str(exc.value)

    def test_leaves_other_parameters_visible(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Redactar de más haría el mensaje inútil para diagnosticar.
        patch_urlopen(monkeypatch, [http_error(401)])
        with pytest.raises(HttpError) as exc:
            client().get("https://example.com/api", params={"apiKey": "x", "regions": "us"})
        assert "regions=us" in str(exc.value)
