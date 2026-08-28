"""Normalización de nombres."""

from __future__ import annotations

import pytest

from sportstar.resolution.names import name_tokens, normalize_name, strip_accents, token_overlap


class TestNormalizeName:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("New York Yankees", "new york yankees"),
            ("St. Louis Cardinals", "st louis cardinals"),
            ("  Montréal   Expos  ", "montreal expos"),
            ("D-backs", "d backs"),
            ("Athletics", "athletics"),
        ],
    )
    def test_known_forms(self, raw: str, expected: str) -> None:
        assert normalize_name(raw) == expected

    def test_is_idempotent(self) -> None:
        once = normalize_name("St. Louis Cardinals")
        assert normalize_name(once) == once

    def test_drops_noise_tokens(self) -> None:
        assert normalize_name("The Chicago Cubs") == "chicago cubs"

    def test_handles_empty_input(self) -> None:
        assert normalize_name("   ") == ""


class TestStripAccents:
    def test_removes_diacritics_without_touching_letters(self) -> None:
        assert strip_accents("Montréal") == "Montreal"
        assert strip_accents("São Paulo") == "Sao Paulo"


class TestTokenOverlap:
    def test_identical_names_score_one(self) -> None:
        assert token_overlap("New York Yankees", "new york yankees") == 1.0

    def test_same_city_different_teams_score_low(self) -> None:
        """El error más fácil de cometer y más caro.

        Yankees y Mets comparten 'new' y 'york': 2 de 4 tokens. Un umbral
        ingenuo los emparejaría y colgaría precios de un partido del otro.
        """
        score = token_overlap("New York Yankees", "New York Mets")
        assert score == pytest.approx(0.5, abs=1e-9)
        from sportstar.resolution.resolver import MIN_TOKEN_OVERLAP

        assert score < MIN_TOKEN_OVERLAP

    def test_abbreviated_city_scores_partially(self) -> None:
        assert 0.0 < token_overlap("NY Yankees", "New York Yankees") < 1.0

    def test_empty_input_scores_zero(self) -> None:
        assert token_overlap("", "New York Yankees") == 0.0

    def test_is_symmetric(self) -> None:
        a, b = "Los Angeles Dodgers", "LA Dodgers"
        assert token_overlap(a, b) == token_overlap(b, a)


class TestNameTokens:
    def test_returns_a_set_of_normalized_tokens(self) -> None:
        assert name_tokens("St. Louis Cardinals") == frozenset({"st", "louis", "cardinals"})
