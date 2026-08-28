"""Conversión de cuotas. Valores calculados a mano, expresados como fracciones
exactas donde existen, para que un test que falla señale un cambio de fórmula y
no un cambio de redondeo.
"""

from __future__ import annotations

import pytest

from sportstar.core.errors import InvalidOddsError, InvalidProbabilityError
from sportstar.core.odds import (
    american_to_decimal,
    american_to_implied,
    break_even_probability,
    decimal_to_american,
    decimal_to_implied,
    implied_to_american,
    implied_to_decimal,
    overround,
    validate_probability,
    vig,
)


class TestAmericanToDecimal:
    @pytest.mark.parametrize(
        ("american", "expected"),
        [
            (-115, 1 + 100 / 115),  # 1.8695652...
            (-110, 21 / 11),  # 1.9090909...
            (-200, 1.5),
            (100, 2.0),
            (-100, 2.0),
            (150, 2.5),
            (250, 3.5),
        ],
    )
    def test_known_values(self, american: float, expected: float) -> None:
        assert american_to_decimal(american) == pytest.approx(expected, abs=1e-12)

    @pytest.mark.parametrize("bad", [0, 99, -99, 50, -1])
    def test_rejects_impossible_range(self, bad: float) -> None:
        # Entre -100 y +100 la cuota describiría un pago inferior al riesgo en
        # ambas direcciones: no existe.
        with pytest.raises(InvalidOddsError):
            american_to_decimal(bad)

    def test_rejects_nan(self) -> None:
        with pytest.raises(InvalidOddsError):
            american_to_decimal(float("nan"))


class TestDecimalToAmerican:
    @pytest.mark.parametrize(
        ("decimal_odds", "expected"),
        [(2.0, 100.0), (2.5, 150.0), (1.5, -200.0), (21 / 11, -110.0), (3.5, 250.0)],
    )
    def test_known_values(self, decimal_odds: float, expected: float) -> None:
        assert decimal_to_american(decimal_odds) == pytest.approx(expected, abs=1e-9)

    @pytest.mark.parametrize("bad", [1.0, 0.5, 0.0, -2.0])
    def test_rejects_non_positive_payout(self, bad: float) -> None:
        with pytest.raises(InvalidOddsError):
            decimal_to_american(bad)

    @pytest.mark.parametrize("american", [-500, -250, -110, 100, 110, 250, 900])
    def test_round_trip(self, american: float) -> None:
        assert decimal_to_american(american_to_decimal(american)) == pytest.approx(
            float(american), abs=1e-9
        )

    def test_minus_100_canonicalizes_to_plus_100(self) -> None:
        # -100 y +100 son el mismo precio (decimal 2.0). La forma canónica es
        # +100, así que el round trip de -100 no es identidad. Documentado aquí
        # para que un feed que envíe -100 no dispare una falsa alarma.
        assert american_to_decimal(-100) == 2.0
        assert decimal_to_american(2.0) == 100.0


class TestImpliedProbability:
    def test_minus_115_is_exactly_115_over_215(self) -> None:
        # 1 / (1 + 100/115) = 115/215. El ejemplo del brief lo muestra como 53.5%.
        assert american_to_implied(-115) == pytest.approx(115 / 215, abs=1e-12)
        assert american_to_implied(-115) == pytest.approx(0.5348837, abs=1e-7)

    def test_minus_110_is_exactly_11_over_21(self) -> None:
        assert american_to_implied(-110) == pytest.approx(11 / 21, abs=1e-12)

    def test_pickem_is_one_half(self) -> None:
        assert decimal_to_implied(2.0) == 0.5

    @pytest.mark.parametrize("p", [0.01, 0.25, 0.5, 0.75, 0.99])
    def test_round_trip(self, p: float) -> None:
        assert decimal_to_implied(implied_to_decimal(p)) == pytest.approx(p, abs=1e-12)
        assert implied_to_american(p) == pytest.approx(
            decimal_to_american(implied_to_decimal(p)), abs=1e-9
        )

    def test_break_even_equals_implied(self) -> None:
        # Son el mismo número. Existen por separado para que nadie lea la implied
        # como la probabilidad real del evento.
        assert break_even_probability(1.9) == decimal_to_implied(1.9)


class TestValidateProbability:
    @pytest.mark.parametrize("bad", [0.0, 1.0, -0.1, 1.5, float("nan")])
    def test_rejects_out_of_open_interval(self, bad: float) -> None:
        # 0 y 1 se rechazan a propósito: implican certeza y en la práctica siempre
        # son un dato corrupto o una división que se va a infinito más adelante.
        with pytest.raises(InvalidProbabilityError):
            validate_probability(bad)

    def test_accepts_interior(self) -> None:
        assert validate_probability(0.5) == 0.5


class TestOverroundAndVig:
    def test_standard_minus_110_both_sides(self) -> None:
        probs = [american_to_implied(-110), american_to_implied(-110)]
        assert overround(probs) == pytest.approx(22 / 21, abs=1e-12)
        assert vig(probs) == pytest.approx(1 / 21, abs=1e-12)  # 4.7619%

    def test_low_vig_sharp_market(self) -> None:
        probs = [american_to_implied(-105), american_to_implied(-105)]
        assert vig(probs) == pytest.approx(2 * (105 / 205) - 1, abs=1e-12)

    def test_requires_at_least_two_sides(self) -> None:
        # Con un solo lado no se puede saber cuánto margen lleva el precio.
        with pytest.raises(InvalidProbabilityError):
            overround([0.55])


class TestNaNHandling:
    def test_decimal_rejects_nan(self) -> None:
        # NaN se propaga en silencio por toda la aritmética y sale al otro lado
        # como un edge de NaN que ninguna comparación detecta.
        with pytest.raises(InvalidOddsError):
            decimal_to_implied(float("nan"))
