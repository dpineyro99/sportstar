"""Closing Line Value."""

from __future__ import annotations

import pytest

from sportstar.core.clv import (
    beat_closing_line,
    clv_price,
    clv_probability,
    evaluate_clv,
    model_beat_close,
)


class TestClvPrice:
    def test_positive_when_taken_price_beats_the_close(self) -> None:
        # Tomamos +100 (2.0) y cerró en -111 (1.90): 5.26% mejor precio.
        assert clv_price(2.0, 1.90) == pytest.approx(2.0 / 1.9 - 1, abs=1e-12)
        assert clv_price(2.0, 1.90) == pytest.approx(0.05263, abs=1e-5)

    def test_negative_when_the_line_moved_against_us(self) -> None:
        assert clv_price(1.90, 2.0) < 0

    def test_zero_when_price_did_not_move(self) -> None:
        assert clv_price(1.95, 1.95) == pytest.approx(0.0, abs=1e-12)


class TestClvProbability:
    def test_positive_when_market_moved_towards_our_side(self) -> None:
        # Apostamos con fair 52%; el mercado cerró valorando ese lado en 55%.
        assert clv_probability(0.52, 0.55) == pytest.approx(0.03, abs=1e-12)

    def test_negative_when_market_moved_away(self) -> None:
        assert clv_probability(0.55, 0.52) == pytest.approx(-0.03, abs=1e-12)

    def test_uses_novig_probabilities_so_it_compares_across_books(self) -> None:
        # Dos books con vig distinto pero la misma fair dan el mismo CLV de
        # probabilidad. Es lo que lo hace comparable entre mercados y deportes.
        assert clv_probability(0.52, 0.55) == clv_probability(0.52, 0.55)


class TestBeatClosingLine:
    def test_strictly_better_price_counts(self) -> None:
        assert beat_closing_line(2.0, 1.9)

    def test_equal_price_does_not_count(self) -> None:
        assert not beat_closing_line(1.9, 1.9)

    def test_worse_price_does_not_count(self) -> None:
        assert not beat_closing_line(1.85, 1.9)


class TestModelBeatClose:
    """La métrica de ARCHITECTURE.md §4.6: validar sin apostar."""

    def test_true_when_model_was_closer_to_the_close_than_the_market(self) -> None:
        # Mercado decía 50%, modelo decía 56%, cerró en 57%. El modelo tenía
        # información que el mercado todavía no había incorporado.
        assert model_beat_close(
            model_prob=0.56, market_fair_prob_at_eval=0.50, closing_fair_prob=0.57
        )

    def test_false_when_the_market_was_closer(self) -> None:
        assert not model_beat_close(
            model_prob=0.65, market_fair_prob_at_eval=0.55, closing_fair_prob=0.56
        )

    def test_tie_counts_as_not_beaten(self) -> None:
        # Ante la duda no acreditamos ventaja al modelo.
        assert not model_beat_close(
            model_prob=0.54, market_fair_prob_at_eval=0.56, closing_fair_prob=0.55
        )

    def test_works_without_any_bet_being_placed(self) -> None:
        # La firma no menciona stake, precio tomado ni resultado: se puede
        # evaluar sobre cada selección de cada evento, apostada o no. Es lo que
        # multiplica la muestra por uno o dos órdenes de magnitud.
        assert model_beat_close(
            model_prob=0.60, market_fair_prob_at_eval=0.50, closing_fair_prob=0.58
        )


class TestEvaluateClv:
    def test_bundles_both_variants(self) -> None:
        result = evaluate_clv(
            taken_decimal=2.0,
            closing_decimal=1.90,
            fair_prob_at_bet=0.50,
            closing_fair_prob=0.5263,
        )
        assert result.beat_closing_line
        assert result.clv_price > 0
        assert result.clv_probability > 0

    def test_price_and_probability_clv_agree_in_sign_for_a_two_way_market(self) -> None:
        # No es una identidad matemática (dependen de books distintos), pero una
        # discrepancia de signo señala un emparejamiento incorrecto de cierres.
        result = evaluate_clv(
            taken_decimal=1.85,
            closing_decimal=2.05,
            fair_prob_at_bet=0.55,
            closing_fair_prob=0.50,
        )
        assert result.clv_price < 0
        assert result.clv_probability < 0
