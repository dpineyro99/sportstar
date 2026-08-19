"""Edge y expected value."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from sportstar.core.edge import (
    breakeven_model_prob,
    edge,
    evaluate,
    expected_roi,
    expected_value,
    structural_edge,
)
from sportstar.core.errors import InvalidProbabilityError
from sportstar.core.odds import american_to_decimal, american_to_implied


class TestBriefExample:
    """Reproduce el ejemplo BEST BET del brief, número a número.

    Celtics ML -115
    Model Probability: 58.7%
    Market Break-even: 53.5%
    Edge: +5.2%
    EV: +9.7%
    """

    MODEL_PROB = 0.587
    AMERICAN = -115

    def test_break_even_is_53_5_percent(self) -> None:
        dec = american_to_decimal(self.AMERICAN)
        assert breakeven_model_prob(dec) == pytest.approx(0.535, abs=5e-4)

    def test_expected_value_is_9_7_percent(self) -> None:
        dec = american_to_decimal(self.AMERICAN)
        assert expected_value(self.MODEL_PROB, dec) == pytest.approx(0.0974, abs=5e-4)

    def test_edge_against_implied_reproduces_the_brief(self) -> None:
        # El brief calcula el edge contra el break-even (la implied, CON vig).
        implied = american_to_implied(self.AMERICAN)
        assert edge(self.MODEL_PROB, implied) == pytest.approx(0.052, abs=5e-4)

    def test_edge_against_fair_probability_is_smaller(self) -> None:
        """Y por eso el sistema no lo calcula así.

        Restar la implied en vez de la fair infla el edge: la implied lleva el vig
        dentro. Con -115/-105 en un mercado real, la fair del lado que apostamos
        queda por debajo de la implied, así que el edge honesto es MAYOR... o
        menor, según el otro lado. Lo que nunca es, es comparable entre mercados.

        Aquí el punto es solo que son números distintos y que confundirlos es
        sistemático, no ocasional.
        """
        implied = american_to_implied(self.AMERICAN)
        # Mercado completo -115 / -105: el book carga vig en ambos lados.
        both = [american_to_implied(-115), american_to_implied(-105)]
        fair_our_side = both[0] / sum(both)

        assert fair_our_side < implied
        assert edge(self.MODEL_PROB, fair_our_side) != pytest.approx(
            edge(self.MODEL_PROB, implied), abs=1e-6
        )


class TestEdge:
    def test_is_a_plain_difference(self) -> None:
        assert edge(0.60, 0.55) == pytest.approx(0.05, abs=1e-12)

    def test_can_be_negative(self) -> None:
        # Un edge negativo se devuelve tal cual: esconder el signo obligaría al
        # llamante a adivinar si el cero significa "sin ventaja" o "no calculado".
        assert edge(0.45, 0.55) == pytest.approx(-0.10, abs=1e-12)

    @pytest.mark.parametrize(("p", "q"), [(0.0, 0.5), (0.5, 1.0), (1.5, 0.5)])
    def test_rejects_impossible_probabilities(self, p: float, q: float) -> None:
        with pytest.raises(InvalidProbabilityError):
            edge(p, q)


class TestStructuralEdge:
    def test_positive_when_best_price_beats_sharp_consensus(self) -> None:
        # Consenso sharp: 55% justo. Mejor precio recreativo: +100 (implied 50%).
        # 5 puntos de ventaja sin modelo alguno — line shopping formalizado.
        assert structural_edge(0.55, 2.0) == pytest.approx(0.05, abs=1e-12)

    def test_negative_when_best_price_is_worse_than_fair(self) -> None:
        assert structural_edge(0.45, 2.0) == pytest.approx(-0.05, abs=1e-12)

    def test_needs_no_model(self) -> None:
        # La firma no acepta model_prob: es la garantía de que el suelo del
        # sistema no depende de la calidad del modelado.
        assert structural_edge(0.60, 1.9) == pytest.approx(0.60 - 1 / 1.9, abs=1e-12)


class TestExpectedValue:
    def test_fair_coin_at_fair_price_is_zero(self) -> None:
        assert expected_value(0.5, 2.0) == pytest.approx(0.0, abs=1e-12)

    def test_edge_at_even_money(self) -> None:
        # p=0.55 a +100: se gana 1 el 55% y se pierde 1 el 45% → +0.10 por unidad.
        assert expected_value(0.55, 2.0) == pytest.approx(0.10, abs=1e-12)

    def test_standard_juice_needs_more_than_a_coin_flip(self) -> None:
        # A -110 hace falta 52.38% solo para empatar.
        dec = american_to_decimal(-110)
        assert expected_value(11 / 21, dec) == pytest.approx(0.0, abs=1e-12)
        assert expected_value(0.50, dec) < 0

    def test_expected_roi_is_the_same_number(self) -> None:
        # Ya está normalizado a stake = 1.
        assert expected_roi(0.6, 1.9) == expected_value(0.6, 1.9)

    def test_breakeven_probability_zeroes_the_ev(self) -> None:
        for dec in (1.5, 1.91, 2.0, 3.4):
            assert expected_value(breakeven_model_prob(dec), dec) == pytest.approx(0.0, abs=1e-12)


class TestEvaluate:
    def test_uses_reference_price_for_fair_and_best_price_for_ev(self) -> None:
        """La separación de ARCHITECTURE.md §4.2, verificada.

        Referencia (sharp) -110; mejor precio ejecutable +100. El EV debe salir
        del +100, no del -110, porque es donde se apuesta de verdad.
        """
        result = evaluate(
            model_prob=0.58,
            market_fair_prob=0.55,
            reference_decimal=american_to_decimal(-110),
            best_decimal=2.0,
        )
        assert result.edge == pytest.approx(0.03, abs=1e-12)
        assert result.expected_value == pytest.approx(expected_value(0.58, 2.0), abs=1e-12)
        assert result.market_implied_prob == pytest.approx(11 / 21, abs=1e-12)
        assert result.market_fair_prob == 0.55
        assert result.break_even_prob == 0.5
        assert result.is_positive_ev

    def test_best_price_defaults_to_reference(self) -> None:
        result = evaluate(model_prob=0.58, market_fair_prob=0.55, reference_decimal=1.9)
        assert result.best_decimal == 1.9

    def test_better_price_never_lowers_ev(self) -> None:
        worse = evaluate(model_prob=0.58, market_fair_prob=0.55, reference_decimal=1.9)
        better = evaluate(
            model_prob=0.58, market_fair_prob=0.55, reference_decimal=1.9, best_decimal=2.05
        )
        assert better.expected_value > worse.expected_value
        # El edge NO cambia: depende de la referencia, no del precio ejecutable.
        assert better.edge == pytest.approx(worse.edge, abs=1e-12)

    def test_is_frozen(self) -> None:
        # Una evaluación es un hecho fechado: si cambia el precio se construye
        # otra, no se muta esta.
        result = evaluate(model_prob=0.58, market_fair_prob=0.55, reference_decimal=1.9)
        with pytest.raises(FrozenInstanceError):
            result.edge = 0.99  # type: ignore[misc]


class TestTotalEdge:
    """La ventaja que de verdad determina el EV."""

    def test_decomposes_into_model_edge_plus_structural_edge(self) -> None:
        from sportstar.core.edge import total_edge

        model_prob, fair, best = 0.58, 0.55, 2.0
        assert total_edge(model_prob, best) == pytest.approx(
            edge(model_prob, fair) + structural_edge(fair, best), abs=1e-12
        )

    def test_is_positive_exactly_when_ev_is_positive(self) -> None:
        from sportstar.core.edge import total_edge

        for model_prob in (0.30, 0.45, 0.49, 0.50, 0.51, 0.70):
            for best in (1.5, 1.91, 2.0, 2.5, 4.0):
                assert (total_edge(model_prob, best) > 0) == (expected_value(model_prob, best) > 0)

    def test_the_market_baseline_has_zero_model_edge_but_nonzero_total(self) -> None:
        """Por qué los gates filtran por la total y no por la de modelo.

        `market_consensus_v1` copia al mercado: su edge de modelo es 0 por
        construcción. Filtrar por él dejaría al baseline sin recomendar nada, y
        con él se iría toda la medición del edge estructural.
        """
        from sportstar.core.edge import total_edge

        fair = 0.55
        model_prob = fair  # el modelo ES el mercado
        assert edge(model_prob, fair) == pytest.approx(0.0, abs=1e-12)
        assert total_edge(model_prob, 2.10) > 0.02
