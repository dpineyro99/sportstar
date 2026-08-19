"""Dimensionamiento de apuesta."""

from __future__ import annotations

import pytest

from sportstar.core.errors import CoreError
from sportstar.core.kelly import (
    SizingMethod,
    StakeConfig,
    fractional_kelly,
    full_kelly,
    recommend_stake,
)
from sportstar.core.odds import american_to_decimal


class TestFullKelly:
    def test_roadmap_reference_case(self) -> None:
        # Criterio de salida de Phase 1: p=0.55 a +100 con fracción 0.25 debe dar
        # exactamente 0.025 del bankroll.
        assert full_kelly(0.55, 2.0) == pytest.approx(0.10, abs=1e-12)
        assert fractional_kelly(0.55, 2.0, 0.25) == pytest.approx(0.025, abs=1e-12)

    def test_zero_at_the_breakeven_probability(self) -> None:
        assert full_kelly(0.5, 2.0) == pytest.approx(0.0, abs=1e-12)
        assert full_kelly(11 / 21, american_to_decimal(-110)) == pytest.approx(0.0, abs=1e-12)

    def test_negative_for_minus_ev_bets(self) -> None:
        # Se devuelve el signo en vez de recortar a 0: recortar aquí escondería
        # la magnitud del error del modelo al llamante.
        assert full_kelly(0.40, 2.0) < 0

    def test_grows_with_edge(self) -> None:
        assert full_kelly(0.70, 2.0) > full_kelly(0.60, 2.0) > full_kelly(0.55, 2.0)

    def test_longshots_get_smaller_fractions_for_the_same_edge(self) -> None:
        # Mismo edge de 5 puntos sobre la break-even, precios muy distintos.
        favourite = full_kelly(0.5 + 0.05, 2.0)  # break-even 50%
        longshot = full_kelly(0.20 + 0.05, 5.0)  # break-even 20%
        assert longshot < favourite


class TestFractionalKelly:
    def test_clips_minus_ev_to_zero(self) -> None:
        assert fractional_kelly(0.40, 2.0) == 0.0

    @pytest.mark.parametrize("bad", [0.0, -0.5, 1.5])
    def test_rejects_invalid_fraction(self, bad: float) -> None:
        with pytest.raises(CoreError):
            fractional_kelly(0.55, 2.0, bad)


class TestRecommendStake:
    def test_default_config_produces_two_and_a_half_units(self) -> None:
        # 0.025 del bankroll con 1 unit = 1% del bankroll.
        stake = recommend_stake(0.55, 2.0)
        assert stake.units == pytest.approx(2.5, abs=1e-9)
        assert stake.method is SizingMethod.KELLY_FRACTIONAL
        assert not stake.was_capped

    def test_cap_applies_to_absurd_kelly_output(self) -> None:
        """El cap no es una preferencia de riesgo, es una defensa.

        p=0.95 a +100 son 22.5 units con cuarto de Kelly. Si el modelo se equivoca
        (y con probabilidades tan extremas es cuando más se equivoca), esa apuesta
        arruina el bankroll. El cap corta a 5 y lo deja registrado.
        """
        stake = recommend_stake(0.95, 2.0)
        assert stake.uncapped_units == pytest.approx(22.5, abs=1e-9)
        assert stake.units == 5.0
        assert stake.was_capped

    def test_minus_ev_bet_gets_zero_units_in_every_method(self) -> None:
        # Apostar plano no arregla un EV negativo.
        for method in SizingMethod:
            stake = recommend_stake(0.40, 2.0, StakeConfig(method=method))
            assert stake.units == 0.0

    def test_flat_ignores_edge_size(self) -> None:
        cfg = StakeConfig(method=SizingMethod.FLAT, flat_stake_units=1.0)
        assert recommend_stake(0.55, 2.0, cfg).units == 1.0
        assert recommend_stake(0.80, 2.0, cfg).units == 1.0

    def test_flat_still_respects_the_cap(self) -> None:
        cfg = StakeConfig(method=SizingMethod.FLAT, flat_stake_units=9.0, max_stake_units=5.0)
        stake = recommend_stake(0.55, 2.0, cfg)
        assert stake.units == 5.0
        assert stake.was_capped

    def test_full_kelly_fraction_is_recorded_even_when_capped(self) -> None:
        # Se persiste para poder detectar después que el modelo produce
        # probabilidades demasiado extremas — síntoma de mala calibración.
        stake = recommend_stake(0.95, 2.0)
        assert stake.full_kelly_fraction == pytest.approx(0.90, abs=1e-9)

    def test_smaller_fraction_means_smaller_stake(self) -> None:
        eighth = recommend_stake(0.60, 2.0, StakeConfig(kelly_fraction=0.125))
        quarter = recommend_stake(0.60, 2.0, StakeConfig(kelly_fraction=0.25))
        assert eighth.units == pytest.approx(quarter.units / 2, abs=1e-9)


class TestStakeConfig:
    @pytest.mark.parametrize(
        "kwargs",
        [
            {"kelly_fraction": 0.0},
            {"kelly_fraction": 1.5},
            {"max_stake_units": 0.0},
            {"max_stake_units": -1.0},
            {"flat_stake_units": 0.0},
        ],
    )
    def test_rejects_invalid_configuration(self, kwargs: dict) -> None:
        with pytest.raises(CoreError):
            StakeConfig(**kwargs)

    def test_defaults_match_the_documented_policy(self) -> None:
        cfg = StakeConfig()
        assert cfg.kelly_fraction == 0.25
        assert cfg.max_stake_units == 5.0
        assert cfg.units_per_bankroll == 100.0
