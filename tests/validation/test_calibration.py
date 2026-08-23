"""Métricas de calibración.

Un modelo que acierta el 70% de los partidos puede perder dinero, y uno que
acierta el 53% puede ganarlo. Lo que decide es si sus probabilidades son
correctas: cuando dice 60%, ¿gana seis de cada diez?
"""

from __future__ import annotations

import pytest

from sportstar.validation.calibration import (
    brier_score,
    calibration_curve,
    evaluate,
    expected_calibration_error,
    log_loss,
)


class TestBrierScore:
    def test_perfect_predictions_score_zero(self) -> None:
        assert brier_score([1.0, 0.0, 1.0], [1, 0, 1]) == 0.0

    def test_always_fifty_fifty_scores_a_quarter(self) -> None:
        """El 0.25 es la referencia del dominio.

        Un modelo que no bate ese número no aporta nada sobre lanzar una moneda.
        """
        assert brier_score([0.5] * 4, [1, 0, 1, 0]) == 0.25

    def test_confident_and_wrong_is_the_worst_case(self) -> None:
        assert brier_score([0.0], [1]) == 1.0

    def test_rejects_mismatched_lengths(self) -> None:
        with pytest.raises(ValueError, match="misma longitud"):
            brier_score([0.5, 0.5], [1])

    def test_rejects_an_empty_set(self) -> None:
        with pytest.raises(ValueError, match="conjunto vacío"):
            brier_score([], [])


class TestLogLoss:
    def test_punishes_confident_errors_harder_than_brier(self) -> None:
        """Un modelo que dice 95% y falla es peligroso para el bankroll de una
        forma que el Brier suaviza: el stake se calcula con la probabilidad."""
        mild_brier = brier_score([0.6], [0]) / brier_score([0.95], [0])
        mild_logloss = log_loss([0.6], [0]) / log_loss([0.95], [0])
        assert mild_logloss < mild_brier

    def test_a_certain_and_wrong_prediction_does_not_return_infinity(self) -> None:
        # Una sola predicción de 0.0 en un partido ganado haría infinita la
        # métrica de todo el conjunto, y con ella inútil el informe entero.
        value = log_loss([0.0], [1])
        assert value > 0 and value != float("inf")

    def test_perfect_predictions_approach_zero(self) -> None:
        assert log_loss([1.0, 0.0], [1, 0]) == pytest.approx(0.0, abs=1e-10)


class TestCalibrationCurve:
    def test_a_well_calibrated_model_matches_its_promises(self) -> None:
        # De 100 predicciones al 70%, ganan 70.
        probabilities = [0.7] * 100
        outcomes = [1] * 70 + [0] * 30
        curve = calibration_curve(probabilities, outcomes)
        assert len(curve) == 1
        assert curve[0].observed_rate == pytest.approx(0.70)
        assert abs(curve[0].gap) < 1e-9

    def test_detects_overconfidence(self) -> None:
        """El diagnóstico que el Brier resume en un número: dice *dónde* falla.

        Sobreestimar solo en los favoritos fuertes es un problema distinto —y con
        distinta solución— que fallar en todas partes.
        """
        probabilities = [0.9] * 100
        outcomes = [1] * 60 + [0] * 40
        curve = calibration_curve(probabilities, outcomes)
        assert curve[0].gap < -0.2  # promete más de lo que entrega

    def test_bins_cover_the_predictions_made(self) -> None:
        curve = calibration_curve([0.15, 0.45, 0.85], [0, 1, 1], bins=10)
        assert len(curve) == 3
        assert sum(b.count for b in curve) == 3

    def test_a_prediction_of_one_lands_in_the_last_bin(self) -> None:
        # Sin el recorte, índice 10 en una lista de 10 tramos.
        curve = calibration_curve([1.0], [1], bins=10)
        assert curve[0].upper == 1.0


class TestExpectedCalibrationError:
    def test_perfect_calibration_scores_zero(self) -> None:
        assert expected_calibration_error([0.7] * 100, [1] * 70 + [0] * 30) == pytest.approx(
            0.0, abs=1e-9
        )

    def test_grows_with_miscalibration(self) -> None:
        good = expected_calibration_error([0.7] * 100, [1] * 70 + [0] * 30)
        bad = expected_calibration_error([0.9] * 100, [1] * 50 + [0] * 50)
        assert bad > good


class TestReport:
    def test_skill_is_zero_when_predicting_the_base_rate(self) -> None:
        """Predecir siempre "el local gana el 52%" no aporta nada, y la métrica
        debe decirlo con un cero, no con un número que parezca bueno."""
        report = evaluate([0.52] * 1000, [1] * 520 + [0] * 480)
        assert report.brier_skill_vs_base_rate == pytest.approx(0.0, abs=1e-3)

    def test_skill_is_positive_for_a_model_that_discriminates(self) -> None:
        probabilities = [0.8] * 500 + [0.2] * 500
        outcomes = [1] * 400 + [0] * 100 + [1] * 100 + [0] * 400
        assert evaluate(probabilities, outcomes).brier_skill_vs_base_rate > 0.3

    def test_skill_is_negative_for_a_model_that_adds_noise(self) -> None:
        # Es un resultado válido y hay que estar dispuesto a aceptarlo.
        probabilities = [0.2] * 500 + [0.8] * 500
        outcomes = [1] * 400 + [0] * 100 + [1] * 100 + [0] * 400
        assert evaluate(probabilities, outcomes).brier_skill_vs_base_rate < 0

    def test_report_carries_the_sample_size(self) -> None:
        # Ninguna métrica de este sistema viaja sin su `n`.
        assert evaluate([0.5] * 42, [1, 0] * 21).n == 42
