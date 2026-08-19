"""Gates de selección."""

from __future__ import annotations

from sportstar.filters.gates import (
    MAX_LINE_AGE_SECONDS,
    MIN_EDGE,
    MIN_REFERENCE_BOOKS,
    GateInput,
    evaluate_gates,
)


def passing(**overrides: object) -> GateInput:
    base = {
        "total_edge": 0.05,
        "expected_value": 0.08,
        "line_age_seconds": 30.0,
        "reference_book_count": 3,
        "data_quality": 1.0,
        "dispersion": 0.005,
        "has_executable_price": True,
    }
    base.update(overrides)
    return GateInput(**base)  # type: ignore[arg-type]


class TestHappyPath:
    def test_a_good_candidate_becomes_a_recommendation(self) -> None:
        result = evaluate_gates(passing())
        assert result.is_recommended
        assert result.failed == ()
        assert "min_edge" in result.passed


class TestIndividualGates:
    def test_thin_edge_is_rejected(self) -> None:
        result = evaluate_gates(passing(total_edge=MIN_EDGE - 0.001, expected_value=0.001))
        assert "min_edge" in result.failed

    def test_negative_ev_is_rejected(self) -> None:
        assert "min_expected_value" in evaluate_gates(passing(expected_value=-0.01)).failed

    def test_stale_price_is_rejected(self) -> None:
        """Un edge calculado sobre un precio de hace media hora no es una
        oportunidad, es un artefacto: el precio ya no está ahí."""
        stale = evaluate_gates(passing(line_age_seconds=MAX_LINE_AGE_SECONDS + 1))
        assert "line_freshness" in stale.failed

    def test_missing_price_age_is_rejected(self) -> None:
        # Desconocer la antigüedad es peor que saber que es alta.
        assert "line_freshness" in evaluate_gates(passing(line_age_seconds=None)).failed

    def test_single_reference_book_is_rejected(self) -> None:
        """Con un solo book no hay consenso, hay una opinión."""
        result = evaluate_gates(passing(reference_book_count=MIN_REFERENCE_BOOKS - 1))
        assert "reference_books" in result.failed

    def test_poor_data_quality_is_rejected(self) -> None:
        assert "data_quality" in evaluate_gates(passing(data_quality=0.5)).failed

    def test_no_executable_price_is_rejected(self) -> None:
        assert "executable_price" in evaluate_gates(passing(has_executable_price=False)).failed

    def test_dispersion_large_relative_to_the_edge_is_rejected(self) -> None:
        # 3 puntos de dispersión frente a 4 de edge: los sharp no se ponen de
        # acuerdo ni en el signo de la ventaja.
        result = evaluate_gates(passing(total_edge=0.04, dispersion=0.03))
        assert "model_agreement" in result.failed

    def test_the_same_dispersion_passes_with_a_larger_edge(self) -> None:
        # La dispersión se juzga relativa al edge, no contra una constante.
        assert evaluate_gates(passing(total_edge=0.12, dispersion=0.03)).is_recommended

    def test_unknown_dispersion_does_not_block(self) -> None:
        assert evaluate_gates(passing(dispersion=None)).is_recommended


class TestReporting:
    def test_all_gates_are_evaluated_not_short_circuited(self) -> None:
        """Evaluarlos todos cuesta lo mismo y deja el conjunto completo de motivos.

        Es lo que permite después contar qué umbral rechaza más y decidir si está
        aportando o estorbando.
        """
        result = evaluate_gates(
            passing(total_edge=0.001, expected_value=-0.5, data_quality=0.1, line_age_seconds=None)
        )
        assert {"min_edge", "min_expected_value", "data_quality", "line_freshness"} <= set(
            result.failed
        )

    def test_every_gate_appears_in_exactly_one_bucket(self) -> None:
        result = evaluate_gates(passing(expected_value=-0.01))
        assert set(result.passed) & set(result.failed) == set()
        assert len(result.passed) + len(result.failed) == 7

    def test_result_carries_the_filter_version(self) -> None:
        # Un cambio de umbral no debe invalidar la lectura del histórico.
        assert evaluate_gates(passing()).version == "v1"
