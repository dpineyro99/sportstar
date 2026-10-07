"""Contrato point-in-time.

El leakage no produce un error, produce resultados *mejores*. Un backtest con
features contaminadas sale precioso, convence, y no se reproduce en paper
trading. Por eso el invariante se verifica, no se confía.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from sportstar.features.base import (
    FeatureSpec,
    FeatureVector,
    LeakageError,
    Observation,
    assert_point_in_time,
    filter_available,
)

T0 = datetime(2026, 8, 20, 18, 0, tzinfo=UTC)


class TestAssertPointInTime:
    def test_accepts_strictly_earlier_data(self) -> None:
        vector = FeatureVector(
            1, T0, {"elo": 1500.0}, latest_observation_at=T0 - timedelta(hours=1)
        )
        assert_point_in_time(vector)

    def test_rejects_later_data(self) -> None:
        vector = FeatureVector(
            1, T0, {"elo": 1500.0}, latest_observation_at=T0 + timedelta(hours=1)
        )
        with pytest.raises(LeakageError, match="no significará nada"):
            assert_point_in_time(vector)

    def test_rejects_simultaneous_data(self) -> None:
        """La igualdad casi siempre delata un `as_of` derivado del propio dato.

        Un hecho observado en el instante del corte no estaba disponible *antes*
        de él.
        """
        vector = FeatureVector(1, T0, {"elo": 1500.0}, latest_observation_at=T0)
        with pytest.raises(LeakageError):
            assert_point_in_time(vector)

    def test_a_vector_without_observations_is_valid(self) -> None:
        # Un equipo sin historial no tiene features contaminadas: no tiene features.
        assert_point_in_time(FeatureVector(1, T0, {}, latest_observation_at=None))


class TestFilterAvailable:
    def test_keeps_only_what_was_known(self) -> None:
        observations = [
            Observation(T0 - timedelta(days=1)),
            Observation(T0 - timedelta(seconds=1)),
            Observation(T0),
            Observation(T0 + timedelta(days=1)),
        ]
        assert len(filter_available(observations, T0)) == 2

    def test_empty_input_is_empty_output(self) -> None:
        assert filter_available([], T0) == []


class TestFeatureVector:
    def test_reports_missing_features(self) -> None:
        vector = FeatureVector(1, T0, {"elo": 1500.0}, missing=("bullpen_fatigue",))
        assert not vector.is_complete
        assert "bullpen_fatigue" in vector.missing

    def test_get_falls_back_without_raising(self) -> None:
        # Un builder que no pudo calcular una feature no debe tirar el pipeline.
        assert FeatureVector(1, T0, {}).get("elo", 1500.0) == 1500.0

    def test_is_frozen(self) -> None:
        from dataclasses import FrozenInstanceError

        vector = FeatureVector(1, T0, {"elo": 1500.0})
        with pytest.raises(FrozenInstanceError):
            vector.as_of = T0  # type: ignore[misc]


class TestFeatureSpec:
    def test_serializes_for_persistence(self) -> None:
        """Sin la spec versionada, un cambio en cómo se calcula una feature deja
        las filas antiguas atribuidas a una definición que ya no existe."""
        spec = FeatureSpec("mlb", "v1", ("elo", "rest_days"), params={"k": 4.0})
        payload = spec.as_dict()
        assert payload["version"] == "v1"
        assert payload["features"] == ["elo", "rest_days"]
        assert payload["params"] == {"k": 4.0}
