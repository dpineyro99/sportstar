"""Diagnóstico de features.

Este módulo existe por un fallo real: un modelo con cinco features de MLB salió
con cuatro coeficientes de signo invertido mientras las métricas parecían
razonables. Las métricas agregadas no lo mostraban; las explicaciones que habría
generado sí habrían mentido.
"""

from __future__ import annotations

import pytest

from sportstar.validation.features import (
    correlation,
    diagnose,
    find_collinear_pairs,
    find_sign_flips,
)


class TestCorrelation:
    def test_identical_series_correlate_perfectly(self) -> None:
        assert correlation([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)

    def test_opposite_series_correlate_negatively(self) -> None:
        assert correlation([1.0, 2.0, 3.0], [3.0, 2.0, 1.0]) == pytest.approx(-1.0)

    def test_a_constant_series_correlates_with_nothing(self) -> None:
        # Sin varianza no hay correlación definida. Devolver 0 en vez de NaN
        # evita que un NaN se cuele en el diagnóstico y lo invalide entero.
        assert correlation([1.0, 1.0, 1.0], [1.0, 2.0, 3.0]) == 0.0

    def test_rejects_mismatched_lengths(self) -> None:
        with pytest.raises(ValueError, match="misma longitud"):
            correlation([1.0, 2.0], [1.0])


class TestCollinearity:
    def test_detects_features_that_measure_the_same_thing(self) -> None:
        columns = {
            "elo": [1.0, 2.0, 3.0, 4.0, 5.0],
            "wins": [1.1, 2.1, 2.9, 4.2, 5.1],  # casi idéntica
            "rest": [1.0, -1.0, 1.0, -1.0, 1.0],  # ortogonal
        }
        pairs = find_collinear_pairs(columns)
        assert len(pairs) == 1
        assert {pairs[0].left, pairs[0].right} == {"elo", "wins"}

    def test_reports_the_worst_pair_first(self) -> None:
        columns = {
            "a": [1.0, 2.0, 3.0, 4.0],
            "b": [1.0, 2.0, 3.0, 4.0],  # r = 1.00
            "c": [1.0, 2.1, 2.9, 4.3],  # r ligeramente menor
        }
        pairs = find_collinear_pairs(columns)
        assert abs(pairs[0].correlation) >= abs(pairs[-1].correlation)

    def test_negative_correlation_counts_too(self) -> None:
        # Dos features que son la misma medida con el signo cambiado son igual
        # de redundantes que dos idénticas.
        columns = {"a": [1.0, 2.0, 3.0, 4.0], "b": [4.0, 3.0, 2.0, 1.0]}
        assert len(find_collinear_pairs(columns)) == 1

    def test_orthogonal_features_are_clean(self) -> None:
        columns = {"a": [1.0, 2.0, 3.0, 4.0], "b": [1.0, -1.0, 1.0, -1.0]}
        assert find_collinear_pairs(columns) == []


class TestSignFlips:
    def test_flags_a_coefficient_that_contradicts_the_data(self) -> None:
        """El fallo que este módulo existe para atrapar.

        La feature sube cuando el local gana, pero el modelo le dio peso
        negativo. La explicación diría lo contrario de lo que muestran los datos.
        """
        columns = {"elo": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]}
        outcomes = [0.0, 0.0, 1.0, 0.0, 1.0, 1.0]
        flips = find_sign_flips(columns, outcomes, {"elo": -0.5})
        assert len(flips) == 1
        assert "lo contrario" in flips[0].message

    def test_an_agreeing_coefficient_is_clean(self) -> None:
        columns = {"elo": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]}
        outcomes = [0.0, 0.0, 1.0, 0.0, 1.0, 1.0]
        assert find_sign_flips(columns, outcomes, {"elo": +0.5}) == []

    def test_ignores_features_without_signal(self) -> None:
        """Si una feature apenas correlaciona con el resultado, el signo de su
        coeficiente es ruido y marcarlo sería una falsa alarma.

        Lo que importa es la contradicción con evidencia detrás.
        """
        # Correlación exactamente 0: la feature alterna con independencia del
        # resultado.
        columns = {"ruido": [1.0, -1.0, 1.0, -1.0]}
        outcomes = [1.0, 1.0, 0.0, 0.0]
        assert correlation(columns["ruido"], outcomes) == pytest.approx(0.0, abs=1e-12)
        assert find_sign_flips(columns, outcomes, {"ruido": -5.0}) == []

    def test_ignores_coefficients_without_a_matching_column(self) -> None:
        assert find_sign_flips({}, [1.0, 0.0], {"desconocida": 1.0}) == []


class TestDiagnose:
    def test_a_clean_feature_set_passes(self) -> None:
        columns = {"elo": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]}
        outcomes = [0.0, 0.0, 1.0, 0.0, 1.0, 1.0]
        report = diagnose(columns, outcomes, {"elo": +0.5})
        assert report.is_interpretable
        assert "ok" in report.render()

    def test_a_sign_flip_makes_the_model_uninterpretable(self) -> None:
        """Un modelo no interpretable puede desplegarse si sus métricas lo
        justifican, pero **no puede generar explicaciones**."""
        columns = {"elo": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]}
        outcomes = [0.0, 0.0, 1.0, 0.0, 1.0, 1.0]
        assert not diagnose(columns, outcomes, {"elo": -0.5}).is_interpretable

    def test_collinearity_alone_does_not_break_interpretability(self) -> None:
        # Es una advertencia, no un veredicto: dos features correlacionadas con
        # coeficientes coherentes siguen explicando bien.
        columns = {
            "a": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
            "b": [1.1, 2.1, 2.9, 4.2, 5.1, 6.0],
        }
        outcomes = [0.0, 0.0, 1.0, 0.0, 1.0, 1.0]
        report = diagnose(columns, outcomes, {"a": +0.3, "b": +0.2})
        assert report.collinear
        assert report.is_interpretable

    def test_reports_features_without_signal(self) -> None:
        columns = {"ruido": [1.0, -1.0, 1.0, -1.0]}
        outcomes = [1.0, 1.0, 0.0, 0.0]
        assert "ruido" in diagnose(columns, outcomes).weak

    def test_works_without_coefficients(self) -> None:
        # Antes de entrenar también sirve: la colinealidad se ve en los datos.
        columns = {"a": [1.0, 2.0, 3.0, 4.0], "b": [1.0, 2.0, 3.0, 4.0]}
        report = diagnose(columns, [1.0, 0.0, 1.0, 0.0])
        assert report.collinear
        assert report.sign_flips == []
