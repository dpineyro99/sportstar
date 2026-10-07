"""Retirada del vig.

El test que más importa aquí es `test_shin_favours_the_favourite_vs_proportional`:
documenta en código la dirección del sesgo del método proporcional, que es sutil,
sistemático y no lo detecta ningún test de humo.
"""

from __future__ import annotations

import pytest

from sportstar.core.errors import InvalidMarketError, InvalidProbabilityError
from sportstar.core.novig import (
    NoVigMethod,
    remove_vig,
    remove_vig_power,
    remove_vig_proportional,
    remove_vig_shin,
    shin_z,
)
from sportstar.core.odds import american_to_implied

# -110/-110: el mercado estándar de dos lados, 4.76% de vig.
STANDARD = [american_to_implied(-110), american_to_implied(-110)]
# Favorito claro con 5% de overround.
ASYMMETRIC = [0.70, 0.35]
# Mercado de tres vías (fútbol).
THREE_WAY = [0.50, 0.30, 0.25]

ALL_METHODS = list(NoVigMethod)


class TestInvariants:
    @pytest.mark.parametrize("method", ALL_METHODS)
    @pytest.mark.parametrize("market", [STANDARD, ASYMMETRIC, THREE_WAY])
    def test_result_sums_to_one(self, method: NoVigMethod, market: list[float]) -> None:
        assert sum(remove_vig(market, method)) == pytest.approx(1.0, abs=1e-12)

    @pytest.mark.parametrize("method", ALL_METHODS)
    @pytest.mark.parametrize("market", [STANDARD, ASYMMETRIC, THREE_WAY])
    def test_all_probabilities_stay_in_open_interval(
        self, method: NoVigMethod, market: list[float]
    ) -> None:
        assert all(0.0 < p < 1.0 for p in remove_vig(market, method))

    @pytest.mark.parametrize("method", ALL_METHODS)
    @pytest.mark.parametrize("market", [STANDARD, ASYMMETRIC, THREE_WAY])
    def test_preserves_ordering(self, method: NoVigMethod, market: list[float]) -> None:
        fair = remove_vig(market, method)
        assert sorted(range(len(market)), key=lambda i: market[i]) == sorted(
            range(len(fair)), key=lambda i: fair[i]
        )

    @pytest.mark.parametrize("method", ALL_METHODS)
    def test_fair_is_below_implied_for_every_side(self, method: NoVigMethod) -> None:
        # Retirar el vig solo puede bajar las probabilidades: el exceso era margen.
        fair = remove_vig(ASYMMETRIC, method)
        assert all(f < raw for f, raw in zip(fair, ASYMMETRIC, strict=True))


class TestSymmetricMarket:
    @pytest.mark.parametrize("method", ALL_METHODS)
    def test_balanced_market_is_a_coin_flip(self, method: NoVigMethod) -> None:
        # Los tres métodos coinciden exactamente cuando el mercado es simétrico.
        # Es el caso donde la elección de método da igual — y por eso no sirve
        # para decidir cuál usar.
        assert remove_vig(STANDARD, method) == pytest.approx([0.5, 0.5], abs=1e-9)

    def test_shin_z_on_balanced_five_percent_market(self) -> None:
        # Verificado a mano: con π = [0.525, 0.525], imponer Σq = 1 lleva a
        # z² - 1.05z + 0.05 = 0, cuya raíz en [0,1) es z = 0.05.
        assert shin_z([0.525, 0.525]) == pytest.approx(0.05, abs=1e-6)


class TestProportional:
    def test_divides_by_overround(self) -> None:
        fair = remove_vig_proportional(ASYMMETRIC)
        total = sum(ASYMMETRIC)
        assert fair == pytest.approx([0.70 / total, 0.35 / total], abs=1e-12)
        assert fair[0] == pytest.approx(2 / 3, abs=1e-12)


class TestShin:
    def test_shin_favours_the_favourite_vs_proportional(self) -> None:
        """El sesgo del método proporcional, documentado en código.

        Los books cargan más margen en el underdog porque el público sobreapuesta
        longshots. El proporcional deja esa distorsión intacta: **sobreestima** la
        probabilidad justa del underdog y **subestima** la del favorito.

        Como `edge = model_prob - fair_prob`, usar el proporcional produce edge
        fantasma en los FAVORITOS (su fair sale demasiado baja), no en los
        underdogs. Es lo contrario de lo que sugiere la intuición.
        """
        prop = remove_vig_proportional(ASYMMETRIC)
        shin = remove_vig_shin(ASYMMETRIC)

        assert shin[0] > prop[0], "Shin devuelve más probabilidad justa al favorito"
        assert shin[1] < prop[1], "Shin devuelve menos probabilidad justa al underdog"

        # Magnitud: ~0.8 puntos en un mercado 0.70/0.35. Pequeño pero sistemático,
        # y del mismo orden que los edges que buscamos (2-5 puntos).
        assert shin[0] == pytest.approx(0.675, abs=1e-3)
        assert prop[0] == pytest.approx(0.6667, abs=1e-3)

    def test_divergence_grows_with_asymmetry(self) -> None:
        # En mercados equilibrados la elección de método es irrelevante; en
        # longshots decide si el edge existe o no.
        def gap(market: list[float]) -> float:
            return abs(remove_vig_shin(market)[0] - remove_vig_proportional(market)[0])

        assert gap([0.90, 0.15]) > gap([0.70, 0.35]) > gap([0.53, 0.52])


class TestPower:
    def test_solves_the_exponent(self) -> None:
        fair = remove_vig_power(ASYMMETRIC)
        assert sum(fair) == pytest.approx(1.0, abs=1e-12)
        # Corrige en la misma dirección que Shin, con distinta magnitud.
        assert fair[0] > remove_vig_proportional(ASYMMETRIC)[0]


class TestRejections:
    @pytest.mark.parametrize("method", ALL_METHODS)
    def test_rejects_market_without_vig(self, method: NoVigMethod) -> None:
        # Overround <= 1 es arbitraje aparente: con precios reales casi siempre
        # es un precio corrupto o dos lados de mercados distintos.
        with pytest.raises(InvalidMarketError):
            remove_vig([0.48, 0.48], method)

    @pytest.mark.parametrize("method", ALL_METHODS)
    def test_rejects_single_side(self, method: NoVigMethod) -> None:
        # Con un solo lado no se puede saber cuánto margen lleva el precio.
        # Estimarlo con una constante sería una invención que llega hasta el edge.
        with pytest.raises(InvalidProbabilityError):
            remove_vig([0.55], method)

    @pytest.mark.parametrize("method", ALL_METHODS)
    def test_rejects_impossible_probability(self, method: NoVigMethod) -> None:
        with pytest.raises(InvalidProbabilityError):
            remove_vig([1.2, 0.3], method)


class TestNumericHelpers:
    """Rutas del solver que los mercados normales no ejercitan."""

    def test_power_method_handles_an_extreme_overround(self) -> None:
        # Un mercado con 80% de overround (props muy cargadas) necesita k ≈ 6.6,
        # por encima del techo inicial del solver: obliga a ampliar el intervalo.
        heavy = [0.90, 0.90]
        fair = remove_vig_power(heavy)
        assert sum(fair) == pytest.approx(1.0, abs=1e-12)
        assert fair == pytest.approx([0.5, 0.5], abs=1e-9)

    def test_bisect_rejects_an_interval_without_a_root(self) -> None:
        from sportstar.core.novig import _bisect

        with pytest.raises(InvalidMarketError, match="no hay raíz"):
            _bisect(lambda x: x + 1.0, 0.0, 1.0)

    def test_bisect_returns_the_midpoint_when_it_runs_out_of_iterations(self) -> None:
        # Con una discontinuidad la bisección no converge; devuelve el punto medio
        # en vez de colgarse. Preferimos un número acotado a un bucle infinito en
        # un worker de producción.
        from sportstar.core.novig import _bisect

        result = _bisect(lambda x: 1.0 if x < 0.5 else -1.0, 0.0, 1.0)
        assert 0.0 <= result <= 1.0
