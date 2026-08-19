"""Agregación de snapshots.

El test central es `test_devig_per_book_then_average_differs_from_the_naive_order`:
demuestra numéricamente por qué el orden de las operaciones importa.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from sportstar.core.novig import NoVigMethod, remove_vig
from sportstar.core.odds import decimal_to_implied
from sportstar.odds import (
    PricePoint,
    best_available,
    book_fair_probabilities,
    closing_points,
    consensus_fair_probabilities,
    line_age_seconds,
    line_movement,
    market_state,
    opening_points,
)

T0 = datetime(2026, 8, 19, 18, 0, tzinfo=UTC)
HOME, AWAY = 1, 2
SHARP_A, SHARP_B = 10, 11
REC = 20
SELECTIONS = (HOME, AWAY)


def two_way(book: int, home: float, away: float, at: datetime = T0) -> list[PricePoint]:
    return [PricePoint(HOME, book, home, at), PricePoint(AWAY, book, away, at)]


class TestMarketState:
    def test_keeps_only_the_latest_price_per_book_and_selection(self) -> None:
        points = two_way(SHARP_A, 1.90, 1.95) + two_way(
            SHARP_A, 2.00, 1.85, T0 + timedelta(minutes=5)
        )
        state = market_state(points)
        assert state[SHARP_A][HOME].price_decimal == 2.00

    def test_as_of_ignores_later_prices(self) -> None:
        """Lo que hace que el backtest use esta misma función y no una paralela.

        Sin este filtro habría que mantener dos caminos de código —uno para
        producción y otro para el replay— que se desincronizan en la primera
        semana.
        """
        points = two_way(SHARP_A, 1.90, 1.95) + two_way(
            SHARP_A, 2.00, 1.85, T0 + timedelta(minutes=5)
        )
        state = market_state(points, as_of=T0 + timedelta(minutes=1))
        assert state[SHARP_A][HOME].price_decimal == 1.90

    def test_unavailable_prices_are_excluded(self) -> None:
        points = [PricePoint(HOME, SHARP_A, 1.90, T0, is_available=False)]
        assert market_state(points) == {}


class TestBookFairProbabilities:
    def test_removes_the_vig_of_a_single_book(self) -> None:
        prices = {p.selection_id: p for p in two_way(SHARP_A, 1.91, 1.95)}
        fair = book_fair_probabilities(prices, SELECTIONS)
        assert fair is not None
        assert sum(fair.values()) == pytest.approx(1.0, abs=1e-12)

    def test_returns_none_when_a_side_is_missing(self) -> None:
        """Sin el mercado completo no se puede saber cuánto margen lleva el precio.

        Rellenar el lado que falta con una constante sería una invención que
        llega intacta hasta el edge.
        """
        prices = {HOME: PricePoint(HOME, SHARP_A, 1.91, T0)}
        assert book_fair_probabilities(prices, SELECTIONS) is None

    def test_returns_none_for_a_corrupt_market(self) -> None:
        # Overround <= 1: precio corrupto o lados de mercados distintos. Se
        # excluye del consenso en vez de contaminarlo.
        prices = {p.selection_id: p for p in two_way(SHARP_A, 2.20, 2.20)}
        assert book_fair_probabilities(prices, SELECTIONS) is None


class TestConsensus:
    def test_averages_across_reference_books(self) -> None:
        points = two_way(SHARP_A, 1.91, 1.95) + two_way(SHARP_B, 1.88, 1.98)
        result = consensus_fair_probabilities(points, SELECTIONS, {SHARP_A, SHARP_B}, as_of=T0)
        assert result is not None
        assert result.book_count == 2
        assert sum(result.fair_probabilities.values()) == pytest.approx(1.0, abs=1e-12)

    def test_recreational_books_are_excluded_from_the_consensus(self) -> None:
        """Los recreativos definen el precio que conseguimos, no la probabilidad justa.

        Incluirlos equivaldría a comparar el mercado consigo mismo, que es
        exactamente el error R1 del audit.
        """
        sharp_only = two_way(SHARP_A, 1.91, 1.95) + two_way(SHARP_B, 1.88, 1.98)
        with_rec = sharp_only + two_way(REC, 2.50, 1.55)

        a = consensus_fair_probabilities(sharp_only, SELECTIONS, {SHARP_A, SHARP_B}, as_of=T0)
        b = consensus_fair_probabilities(with_rec, SELECTIONS, {SHARP_A, SHARP_B}, as_of=T0)
        assert a is not None and b is not None
        assert a.fair_probabilities == b.fair_probabilities

    def test_devig_per_book_then_average_differs_from_the_naive_order(self) -> None:
        """Por qué el orden de las operaciones importa.

        Correcto:  quitar el vig a cada book por separado, luego promediar.
        Ingenuo:   promediar las implied con vig, luego quitar el vig.

        No son equivalentes cuando los books cargan márgenes distintos, y el
        ingenuo mezcla el margen con la señal. Aquí SHARP_A lleva ~4.7% de vig y
        SHARP_B ~2.5%: el resultado difiere de forma medible y siempre en la
        misma dirección.
        """
        book_a = (1.91, 1.95)  # vig alto
        book_b = (2.02, 1.99)  # vig bajo
        points = two_way(SHARP_A, *book_a) + two_way(SHARP_B, *book_b)

        correct = consensus_fair_probabilities(points, SELECTIONS, {SHARP_A, SHARP_B}, as_of=T0)
        assert correct is not None

        naive_implied = [
            (decimal_to_implied(book_a[0]) + decimal_to_implied(book_b[0])) / 2,
            (decimal_to_implied(book_a[1]) + decimal_to_implied(book_b[1])) / 2,
        ]
        naive = remove_vig(naive_implied, NoVigMethod.PROPORTIONAL)

        assert correct.fair_probabilities[HOME] != pytest.approx(naive[0], abs=1e-6)

    def test_returns_none_when_no_reference_book_has_the_full_market(self) -> None:
        points = [PricePoint(HOME, SHARP_A, 1.91, T0)]
        assert consensus_fair_probabilities(points, SELECTIONS, {SHARP_A}, as_of=T0) is None

    def test_a_book_missing_a_side_is_skipped_not_fatal(self) -> None:
        points = [*two_way(SHARP_A, 1.91, 1.95), PricePoint(HOME, SHARP_B, 1.88, T0)]
        result = consensus_fair_probabilities(points, SELECTIONS, {SHARP_A, SHARP_B}, as_of=T0)
        assert result is not None
        assert result.books_used == (SHARP_A,)


class TestDispersion:
    def test_zero_when_books_agree(self) -> None:
        points = two_way(SHARP_A, 1.91, 1.95) + two_way(SHARP_B, 1.91, 1.95)
        result = consensus_fair_probabilities(points, SELECTIONS, {SHARP_A, SHARP_B}, as_of=T0)
        assert result is not None
        assert result.dispersion(HOME) == pytest.approx(0.0, abs=1e-12)

    def test_grows_when_books_disagree(self) -> None:
        close = two_way(SHARP_A, 1.91, 1.95) + two_way(SHARP_B, 1.92, 1.94)
        far = two_way(SHARP_A, 1.60, 2.40) + two_way(SHARP_B, 2.30, 1.65)
        a = consensus_fair_probabilities(close, SELECTIONS, {SHARP_A, SHARP_B}, as_of=T0)
        b = consensus_fair_probabilities(far, SELECTIONS, {SHARP_A, SHARP_B}, as_of=T0)
        assert a is not None and b is not None
        assert b.dispersion(HOME) > a.dispersion(HOME)

    def test_single_book_reports_zero_dispersion(self) -> None:
        """0.0 con un solo book no significa acuerdo: significa que no hay con
        quién discrepar. Quien lo consuma debe mirar `book_count`, y por eso los
        gates exigen un mínimo de books por separado."""
        points = two_way(SHARP_A, 1.91, 1.95)
        result = consensus_fair_probabilities(points, SELECTIONS, {SHARP_A}, as_of=T0)
        assert result is not None
        assert result.book_count == 1
        assert result.dispersion(HOME) == 0.0


class TestBestAvailable:
    def test_picks_the_highest_decimal_among_executable_books(self) -> None:
        points = two_way(REC, 2.10, 1.80) + two_way(REC + 1, 2.05, 1.85)
        best = best_available(points, HOME, {REC, REC + 1}, as_of=T0)
        assert best is not None and best.price_decimal == 2.10

    def test_ignores_books_where_we_cannot_bet(self) -> None:
        """Un precio mejor en un book inaccesible no es un edge, es una anécdota."""
        points = two_way(SHARP_A, 2.50, 1.55) + two_way(REC, 2.10, 1.80)
        best = best_available(points, HOME, {REC}, as_of=T0)
        assert best is not None and best.price_decimal == 2.10

    def test_returns_none_without_executable_prices(self) -> None:
        assert best_available(two_way(SHARP_A, 1.91, 1.95), HOME, {REC}, as_of=T0) is None

    def test_ties_break_deterministically(self) -> None:
        # El backtest debe ser reproducible: un empate no puede resolverse por
        # el orden en que llegaron las filas.
        points = two_way(REC + 1, 2.10, 1.80) + two_way(REC, 2.10, 1.80)
        first = best_available(points, HOME, {REC, REC + 1}, as_of=T0)
        second = best_available(list(reversed(points)), HOME, {REC, REC + 1}, as_of=T0)
        assert first is not None and second is not None
        assert first.sportsbook_id == second.sportsbook_id == REC


class TestOpeningAndClosing:
    def test_opening_is_the_first_price_seen(self) -> None:
        points = two_way(SHARP_A, 1.90, 1.95, T0) + two_way(
            SHARP_A, 2.00, 1.85, T0 + timedelta(hours=2)
        )
        opening = {p.selection_id: p for p in opening_points(points)}
        assert opening[HOME].price_decimal == 1.90

    def test_closing_excludes_prices_at_or_after_first_pitch(self) -> None:
        """Estrictamente anterior: un precio capturado en el instante del inicio
        ya puede reflejar lo que pasa en el campo."""
        start = T0 + timedelta(hours=1)
        points = (
            two_way(SHARP_A, 1.90, 1.95, T0)
            + two_way(SHARP_A, 1.95, 1.90, start - timedelta(seconds=30))
            + two_way(SHARP_A, 3.00, 1.40, start)
        )
        closing = {p.selection_id: p for p in closing_points(points, start)}
        assert closing[HOME].price_decimal == 1.95

    def test_closing_is_empty_when_nothing_was_captured_in_time(self) -> None:
        # Muestra irrecuperable: es un incidente de Data Health, no un vacío
        # cualquiera.
        start = T0
        assert closing_points(two_way(SHARP_A, 1.90, 1.95, T0), start) == []


class TestLineMovement:
    def test_returns_the_series_in_chronological_order(self) -> None:
        points = [
            PricePoint(HOME, SHARP_A, 2.00, T0 + timedelta(hours=1)),
            PricePoint(HOME, SHARP_A, 1.90, T0),
            PricePoint(HOME, SHARP_B, 1.85, T0),
        ]
        series = line_movement(points, HOME, SHARP_A)
        assert [p.price_decimal for p in series] == [1.90, 2.00]


class TestLineAge:
    def test_measures_staleness_in_seconds(self) -> None:
        point = PricePoint(HOME, REC, 2.0, T0)
        assert line_age_seconds(point, T0 + timedelta(minutes=10)) == pytest.approx(600.0)
