"""Confidence Score v0."""

from __future__ import annotations

import pytest

from sportstar.filters.confidence import (
    CONFIDENCE_VERSION,
    NEUTRAL,
    WEIGHTS,
    compute_confidence,
    score_edge_in_sigmas,
    score_line_freshness,
    score_model_agreement,
    score_sample_size,
)


class TestWeights:
    def test_weights_sum_to_one(self) -> None:
        assert sum(WEIGHTS.values()) == pytest.approx(1.0, abs=1e-12)

    def test_version_is_zero_until_recalibrated(self) -> None:
        # Sin histórico contra el que calibrar, cualquier peso es una convención.
        # Se versiona para poder comparar versiones sobre el mismo histórico
        # cuando Phase 4 dé datos.
        assert CONFIDENCE_VERSION == 0


class TestComponents:
    def test_edge_in_sigmas_rewards_precision_not_just_size(self) -> None:
        """Tres puntos de edge no valen lo mismo según lo seguro que esté el modelo."""
        precise = score_edge_in_sigmas(0.03, uncertainty=0.005)
        vague = score_edge_in_sigmas(0.03, uncertainty=0.05)
        assert precise is not None and vague is not None
        assert precise > vague

    def test_edge_in_sigmas_is_missing_without_uncertainty(self) -> None:
        assert score_edge_in_sigmas(0.03, uncertainty=None) is None
        assert score_edge_in_sigmas(0.03, uncertainty=0.0) is None

    def test_edge_in_sigmas_saturates(self) -> None:
        assert score_edge_in_sigmas(0.50, uncertainty=0.001) == 1.0

    def test_model_agreement_is_relative_to_the_edge(self) -> None:
        # 1 punto de dispersión es irrelevante frente a 6 de edge y demoledor
        # frente a 1.5.
        big_edge = score_model_agreement(0.01, edge=0.06)
        small_edge = score_model_agreement(0.01, edge=0.015)
        assert big_edge is not None and small_edge is not None
        assert big_edge > small_edge

    def test_perfect_agreement_scores_one(self) -> None:
        assert score_model_agreement(0.0, edge=0.03) == 1.0

    def test_sample_size_saturates(self) -> None:
        assert score_sample_size(8) is not None
        assert score_sample_size(500) == 1.0
        assert score_sample_size(0) == 0.0

    def test_line_freshness_decays_with_age(self) -> None:
        fresh = score_line_freshness(0.0)
        old = score_line_freshness(300.0)
        expired = score_line_freshness(9999.0)
        assert fresh == 1.0
        assert old is not None and 0.0 < old < 1.0
        assert expired == 0.0


class TestMissingComponents:
    def test_missing_components_count_as_neutral_not_excluded(self) -> None:
        """Excluir y renormalizar haría que saber MENOS puntuase MÁS.

        Es el fallo silencioso de casi todos los scores compuestos: la apuesta
        sobre la que no tenemos datos acaba pareciendo la mejor.
        """
        informed = compute_confidence(
            edge=0.03,
            uncertainty=0.005,
            dispersion=0.002,
            data_quality=1.0,
            sample_size=100,
            line_age_seconds=10.0,
            historical_calibration=1.0,
        )
        ignorant = compute_confidence(edge=0.03)

        assert informed.score > ignorant.score
        assert ignorant.components["sample_size"] == NEUTRAL

    def test_missing_components_are_reported(self) -> None:
        result = compute_confidence(edge=0.03, data_quality=1.0)
        assert "sample_size" in result.missing_components
        assert "historical_calibration" in result.missing_components
        assert not result.is_fully_informed

    def test_a_fully_informed_result_says_so(self) -> None:
        result = compute_confidence(
            edge=0.03,
            uncertainty=0.005,
            dispersion=0.002,
            data_quality=1.0,
            sample_size=100,
            line_age_seconds=10.0,
            historical_calibration=0.9,
        )
        assert result.is_fully_informed


class TestScore:
    def test_score_is_within_zero_and_ten(self) -> None:
        best = compute_confidence(
            edge=0.10,
            uncertainty=0.001,
            dispersion=0.0,
            data_quality=1.0,
            sample_size=1000,
            line_age_seconds=0.0,
            historical_calibration=1.0,
        )
        worst = compute_confidence(
            edge=0.001,
            uncertainty=0.5,
            dispersion=0.5,
            data_quality=0.0,
            sample_size=0,
            line_age_seconds=99999.0,
            historical_calibration=0.0,
        )
        assert best.score == 10.0
        assert worst.score == 0.0

    def test_score_is_rounded_to_one_decimal(self) -> None:
        # Más precisión sería falsa: los pesos no están calibrados y un 8.37
        # sugiere una exactitud que no existe.
        result = compute_confidence(edge=0.03, uncertainty=0.007, dispersion=0.001)
        assert result.score == round(result.score, 1)

    def test_breakdown_is_persisted_for_audit(self) -> None:
        result = compute_confidence(edge=0.03, data_quality=0.9)
        assert set(result.components) == set(WEIGHTS)
        assert result.version == CONFIDENCE_VERSION

    def test_high_confidence_in_a_bad_bet_is_expected(self) -> None:
        """Mide certeza en la estimación, no atractivo de la apuesta.

        Se puede estar muy seguro de que algo es mala idea. Quien decide si se
        apuesta son los gates; el confidence ordena lo que ya pasó ese corte.
        """
        result = compute_confidence(
            edge=-0.08,
            uncertainty=0.002,
            dispersion=0.001,
            data_quality=1.0,
            sample_size=200,
            line_age_seconds=5.0,
        )
        assert result.score > 7.0
