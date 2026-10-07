"""Resolución de equipos.

El principio que gobierna estos tests: **un emparejamiento incorrecto es peor que
ninguno.** El que no empareja acaba en la cola de revisión y se ve; el que
empareja mal se convierte en un dato silenciosamente falso.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from sportstar.db.catalog import ExternalId, League, Team
from sportstar.db.enums import EntityType
from sportstar.db.ops import UnmatchedEntity
from sportstar.resolution import TeamResolver
from sportstar.seeds import seed_catalog


@pytest.fixture
def resolver(session: Session) -> TeamResolver:
    seed_catalog(session)
    session.flush()
    league = session.query(League).filter_by(key="mlb").one()
    return TeamResolver(session, league.id)


def team_id(session: Session, key: str) -> int:
    return session.query(Team).filter_by(key=key).one().id


class TestExactMatches:
    def test_resolves_the_catalog_name(self, resolver: TeamResolver, session: Session) -> None:
        result = resolver.resolve("New York Yankees", provider="odds-api")
        assert result.entity_id == team_id(session, "NYY")
        assert result.method == "name"
        assert result.confidence == 1.0

    def test_resolves_the_key(self, resolver: TeamResolver, session: Session) -> None:
        assert resolver.resolve("NYY", provider="p").entity_id == team_id(session, "NYY")

    def test_resolves_the_abbreviation(self, resolver: TeamResolver, session: Session) -> None:
        # TB es la abreviatura de Tampa Bay Rays, cuya key es TBR.
        assert resolver.resolve("TB", provider="p").entity_id == team_id(session, "TBR")

    def test_is_insensitive_to_case_punctuation_and_accents(
        self, resolver: TeamResolver, session: Session
    ) -> None:
        expected = team_id(session, "STL")
        for variant in ("st. louis cardinals", "ST LOUIS CARDINALS", "St.  Louis   Cardinals"):
            assert resolver.resolve(variant, provider="p").entity_id == expected


class TestExternalIds:
    def test_external_id_wins_over_the_name(self, resolver: TeamResolver, session: Session) -> None:
        """El ID del proveedor es la señal más fiable y va primero.

        Aunque el nombre entrante sea basura, si el proveedor nos dio un ID que
        ya hemos visto, ese ID manda.
        """
        yankees = team_id(session, "NYY")
        session.add(
            ExternalId(
                entity_type=EntityType.TEAM,
                entity_id=yankees,
                provider="odds-api",
                provider_id="12345",
            )
        )
        session.flush()

        result = resolver.resolve("texto irreconocible", provider="odds-api", provider_id="12345")
        assert result.entity_id == yankees
        assert result.method == "external_id"

    def test_external_id_of_another_provider_does_not_apply(
        self, resolver: TeamResolver, session: Session
    ) -> None:
        session.add(
            ExternalId(
                entity_type=EntityType.TEAM,
                entity_id=team_id(session, "NYY"),
                provider="mlb-stats",
                provider_id="147",
            )
        )
        session.flush()
        assert not resolver.resolve("???", provider="odds-api", provider_id="147").matched


class TestAliases:
    def test_learned_alias_resolves_afterwards(
        self, resolver: TeamResolver, session: Session
    ) -> None:
        yankees = team_id(session, "NYY")
        assert not resolver.resolve("Bronx Bombers", provider="p").matched

        resolver.learn_alias("Bronx Bombers", yankees, source="manual")
        session.flush()

        result = resolver.resolve("Bronx Bombers", provider="p")
        assert result.entity_id == yankees
        assert result.method == "alias"


class TestRefusals:
    def test_does_not_match_a_different_team_in_the_same_city(
        self, resolver: TeamResolver, session: Session
    ) -> None:
        """Yankees vs Mets: 50% de tokens en común, equipos distintos.

        Este es el emparejamiento que un umbral laxo produce y que después
        aparece como precios colgados del partido equivocado.
        """
        mets = team_id(session, "NYM")
        result = resolver.resolve("New York Mets", provider="p")
        assert result.entity_id == mets  # exacto, no por solapamiento

        # Y una variante ambigua no se resuelve a ninguno de los dos.
        ambiguous = resolver.resolve("New York", provider="p")
        assert not ambiguous.matched

    def test_returns_unmatched_for_unknown_names(self, resolver: TeamResolver) -> None:
        assert not resolver.resolve("Barcelona", provider="p").matched

    def test_returns_unmatched_for_empty_input(self, resolver: TeamResolver) -> None:
        assert not resolver.resolve("   ", provider="p").matched

    def test_never_raises(self, resolver: TeamResolver) -> None:
        # Un job de odds no puede caerse porque un proveedor mande basura en un
        # campo: lo que no resuelve va a la cola y el resto del slate se procesa.
        for junk in ("", "   ", "???", "12345", "\n\t"):
            assert resolver.resolve(junk, provider="p").entity_id is None


class TestUnmatchedQueue:
    def test_records_what_it_could_not_resolve(
        self, resolver: TeamResolver, session: Session
    ) -> None:
        resolver.record_unmatched("Barcelona", provider="odds-api", context={"event": "x"})
        session.flush()
        row = session.query(UnmatchedEntity).one()
        assert row.raw_value == "Barcelona"
        assert row.occurrences == 1

    def test_repeated_failures_increment_instead_of_duplicating(
        self, resolver: TeamResolver, session: Session
    ) -> None:
        # Lo que importa para priorizar la revisión es cuántas veces nos ha
        # costado datos, no cuántos strings distintos hay.
        for _ in range(3):
            resolver.record_unmatched("Barcelona", provider="odds-api")
            session.flush()
        row = session.query(UnmatchedEntity).one()
        assert row.occurrences == 3
        assert row.last_seen_at >= row.first_seen_at


class TestSubsetMatching:
    """El nombre del catálogo cabe entero dentro del entrante.

    Es la regla que hace trabajo real con nombres cortos: el umbral de
    solapamiento (0.80) es inalcanzable para nombres de 2-3 tokens sin que la
    coincidencia exacta ya lo haya resuelto.
    """

    def test_matches_a_decorated_provider_name(
        self, resolver: TeamResolver, session: Session
    ) -> None:
        result = resolver.resolve("New York Yankees Baseball Club", provider="p")
        assert result.entity_id == team_id(session, "NYY")
        assert result.method == "token_subset"

    def test_does_not_match_a_prefix_that_omits_the_team(self, resolver: TeamResolver) -> None:
        """'New York' no contiene los tres tokens de 'New York Yankees'.

        Es subconjunto, no solapamiento: por eso la ciudad sola no resuelve a
        ninguno de sus dos equipos.
        """
        assert not resolver.resolve("New York", provider="p").matched

    def test_does_not_confuse_two_teams_from_the_same_city(
        self, resolver: TeamResolver, session: Session
    ) -> None:
        result = resolver.resolve("New York Mets Baseball", provider="p")
        assert result.entity_id == team_id(session, "NYM")

    def test_confidence_reflects_how_much_of_the_name_was_matched(
        self, resolver: TeamResolver
    ) -> None:
        tight = resolver.resolve("New York Yankees Club", provider="p")
        loose = resolver.resolve("New York Yankees Club Of Greater New Jersey", provider="p")
        assert tight.confidence > loose.confidence
