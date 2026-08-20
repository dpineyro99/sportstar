"""API HTTP.

La API es el contrato del sistema: lo que se verifica aquí no es solo que
responda, sino que **no permita construir un cliente que mienta**. Las tres
probabilidades llegan separadas, ninguna métrica viaja sin su muestra, y las
razones no pueden inventarse.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient


class TestLiveness:
    def test_responds_without_touching_the_database(self, client: TestClient) -> None:
        body = client.get("/v1/health").json()
        assert body["status"] == "ok"
        assert body["time"].endswith("+00:00")  # UTC explícito


class TestRecommendations:
    def test_lists_the_recommendation(self, client: TestClient) -> None:
        body = client.get("/v1/recommendations").json()
        assert body["total"] == 1
        assert len(body["items"]) == 1

    def test_the_three_probabilities_travel_separately(self, client: TestClient) -> None:
        """Fusionarlas en la API reintroduciría el error que el esquema evita.

        La implícita lleva vig; la justa no. Un cliente que reciba un solo número
        acabará calculando el edge contra el precio equivocado.
        """
        candidate = client.get("/v1/recommendations").json()["items"][0]["candidate"]
        assert candidate["market_implied_probability"] != candidate["market_fair_probability"]
        assert {
            "model_probability",
            "market_implied_probability",
            "market_fair_probability",
        } <= set(candidate)

    def test_edge_decomposition_is_exposed(self, client: TestClient) -> None:
        candidate = client.get("/v1/recommendations").json()["items"][0]["candidate"]
        assert candidate["edge"] == 0.0  # market_consensus_v1 no tiene edge de modelo
        assert candidate["structural_edge"] > 0.02
        assert abs(candidate["total_edge"] - candidate["structural_edge"]) < 1e-9

    def test_ordered_by_confidence_descending(self, client: TestClient) -> None:
        # Es la pantalla principal: la calidad manda sobre la hora.
        scores = [r["confidence_score"] for r in client.get("/v1/recommendations").json()["items"]]
        assert scores == sorted(scores, reverse=True)

    def test_confidence_version_is_exposed(self, client: TestClient) -> None:
        # Un 8.6 con `confidence_version: 0` es un número provisional, y el
        # cliente debe poder decirlo.
        item = client.get("/v1/recommendations").json()["items"][0]
        assert item["confidence_version"] == 0
        assert 0 <= item["confidence_score"] <= 10

    def test_filters_by_minimum_confidence(self, client: TestClient) -> None:
        assert client.get("/v1/recommendations?min_confidence=10").json()["total"] == 0
        assert client.get("/v1/recommendations?min_confidence=0").json()["total"] == 1

    def test_filters_by_sport(self, client: TestClient) -> None:
        assert client.get("/v1/recommendations?sport=mlb").json()["total"] == 1
        assert client.get("/v1/recommendations?sport=nba").json()["total"] == 0

    def test_filters_by_date(self, client: TestClient) -> None:
        assert client.get("/v1/recommendations?event_date=2026-08-19").json()["total"] == 1
        assert client.get("/v1/recommendations?event_date=2026-08-20").json()["total"] == 0

    def test_rejects_an_oversized_limit(self, client: TestClient) -> None:
        # Un cliente móvil con mala conexión no debe poder pedir el histórico
        # entero y quedarse colgado.
        assert client.get("/v1/recommendations?limit=99999").status_code == 422


class TestRecommendationDetail:
    def test_returns_the_recommendation_with_its_reasons(self, client: TestClient) -> None:
        listed = client.get("/v1/recommendations").json()["items"][0]
        detail = client.get(f"/v1/recommendations/{listed['id']}").json()
        assert detail["id"] == listed["id"]
        assert detail["reasons"]

    def test_reasons_come_from_real_contributions(self, client: TestClient) -> None:
        """Nunca se inventa un factor que el modelo no usó.

        `market_consensus_v1` solo sabe dos cosas y su edge de modelo es 0, así
        que solo puede dar una razón. Un sistema que rellenara con factores
        plausibles convertiría una coincidencia en una convicción.
        """
        listed = client.get("/v1/recommendations").json()["items"][0]
        reasons = client.get(f"/v1/recommendations/{listed['id']}").json()["reasons"]
        assert len(reasons) == 1
        assert reasons[0]["factor_key"] == "structural_edge"
        assert reasons[0]["source"] == "market"
        assert reasons[0]["contribution"] > 0.02

    def test_unknown_id_is_a_404(self, client: TestClient) -> None:
        assert client.get("/v1/recommendations/999999").status_code == 404


class TestCandidates:
    def test_lists_every_candidate_including_rejected(self, client: TestClient) -> None:
        """Ver qué se descartó y con qué números es lo que permite auditar el
        filtro por separado del modelo."""
        body = client.get("/v1/candidates").json()
        assert body["total"] == 2
        assert sum(1 for c in body["items"] if c["is_recommended"]) == 1

    def test_marks_which_ones_became_recommendations(self, client: TestClient) -> None:
        items = client.get("/v1/candidates").json()["items"]
        recommended = [c for c in items if c["is_recommended"]]
        rejected = [c for c in items if not c["is_recommended"]]
        assert recommended[0]["expected_roi"] > 0
        assert rejected[0]["expected_roi"] < 0

    def test_filters_by_minimum_edge(self, client: TestClient) -> None:
        assert client.get("/v1/candidates?min_edge=0.5").json()["total"] == 0

    def test_paginates(self, client: TestClient) -> None:
        page = client.get("/v1/candidates?limit=1&offset=0").json()
        assert len(page["items"]) == 1
        assert page["total"] == 2


class TestPerformance:
    def test_reports_sample_size_even_with_no_bets(self, client: TestClient) -> None:
        """Una métrica sin su `n` invita a leer ruido como señal."""
        body = client.get("/v1/performance").json()
        assert body["n_bets"] == 0
        assert body["roi"] is None
        assert body["metrics_are_interpretable"] is False

    def test_says_explicitly_why_the_sample_is_not_interpretable(self, client: TestClient) -> None:
        body = client.get("/v1/performance").json()
        assert body["interpretation_note"]
        assert "Sin datos" in body["interpretation_note"]

    def test_counts_candidates_separately_from_bets(self, client: TestClient) -> None:
        # La validación del modelo usa todos los candidates; la del filtro solo
        # las apuestas. Mezclarlas confunde muestras que difieren en dos órdenes
        # de magnitud.
        body = client.get("/v1/performance").json()
        assert body["n_candidates"] == 2
        assert body["n_bets"] == 0

    def test_rejects_an_unknown_window(self, client: TestClient) -> None:
        assert client.get("/v1/performance?window=siempre").status_code == 422

    def test_accepts_the_documented_windows(self, client: TestClient) -> None:
        for window in ("7d", "30d", "90d", "all"):
            assert client.get(f"/v1/performance?window={window}").status_code == 200


class TestModels:
    def test_lists_the_registered_model(self, client: TestClient) -> None:
        models = client.get("/v1/models").json()
        assert len(models) == 1
        assert models[0]["name"] == "market_consensus"
        assert models[0]["version"] == "v1"
        assert models[0]["is_active"] is True


class TestDataHealth:
    def test_reports_health_by_severity(self, client: TestClient) -> None:
        body = client.get("/v1/health/data").json()
        assert set(body) == {"is_healthy", "checked_at", "critical", "warning", "info"}
        assert isinstance(body["critical"], list)

    def test_only_criticals_break_health(self, client: TestClient) -> None:
        body = client.get("/v1/health/data").json()
        assert body["is_healthy"] == (len(body["critical"]) == 0)


class TestContract:
    def test_timestamps_are_utc_with_explicit_offset(self, client: TestClient) -> None:
        # Un cliente en otra zona horaria no puede tener que adivinar.
        candidate = client.get("/v1/recommendations").json()["items"][0]["candidate"]
        # Pydantic serializa UTC como sufijo `Z`; ambas formas son ISO-8601
        # válidas. Lo que importa es que el designador de zona esté, no cuál sea:
        # un cliente en otra zona horaria no puede tener que adivinar.
        for value in (candidate["as_of"], candidate["event"]["start_time"]):
            assert value.endswith(("Z", "+00:00"))
            assert datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo == UTC

    def test_probabilities_are_fractions_not_percentages(self, client: TestClient) -> None:
        # Mezclar unidades en el transporte es cómo aparecen los errores de x100.
        candidate = client.get("/v1/recommendations").json()["items"][0]["candidate"]
        assert 0 < candidate["model_probability"] < 1
        assert 0 < candidate["market_fair_probability"] < 1

    def test_openapi_schema_is_generated(self, client: TestClient) -> None:
        # Es lo que permitirá generar el cliente de la PWA sin escribirlo a mano.
        schema = client.get("/openapi.json").json()
        assert "/v1/recommendations" in schema["paths"]

    def test_the_api_is_read_only(self, client: TestClient) -> None:
        """Las recomendaciones las produce el pipeline, no una petición HTTP.

        Mantener la API de lectura impide que un cliente altere el histórico de
        decisiones, que debe ser inmutable para poder auditarlo.
        """
        from sportstar.api.app import app

        methods = {m for route in app.routes for m in getattr(route, "methods", set())}
        assert methods <= {"GET", "HEAD", "OPTIONS"}
