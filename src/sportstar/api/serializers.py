"""Conversión de filas de la base a esquemas de respuesta."""

from __future__ import annotations

from sqlalchemy.orm import Session

from ..core.odds import decimal_to_american
from ..db.betting import Candidate, Recommendation
from ..db.catalog import League, Sport, Sportsbook, Team
from ..db.events import Event
from ..db.markets import Market, Selection
from .schemas import CandidateOut, EventOut, PriceOut, ReasonOut, RecommendationOut


class Catalog:
    """Cachea el catálogo durante una petición.

    Serializar treinta recomendaciones haría cientos de consultas de una fila
    para resolver nombres de equipo y de book. El catálogo es pequeño y estable:
    se trae entero una vez.
    """

    def __init__(self, session: Session) -> None:
        self.teams = {t.id: t for t in session.query(Team).all()}
        self.books = {b.id: b for b in session.query(Sportsbook).all()}
        self.leagues = {league.id: league for league in session.query(League).all()}
        self.sports = {s.id: s for s in session.query(Sport).all()}
        self.markets = {m.id: m for m in session.query(Market).all()}

    def team_name(self, team_id: int | None) -> str:
        team = self.teams.get(team_id) if team_id is not None else None
        return team.name if team else "?"

    def book_name(self, book_id: int | None) -> str:
        book = self.books.get(book_id) if book_id is not None else None
        return book.name if book else "?"


def serialize_event(event: Event, catalog: Catalog) -> EventOut:
    league = catalog.leagues.get(event.league_id)
    sport = catalog.sports.get(league.sport_id) if league else None
    return EventOut(
        id=event.id,
        sport=sport.key if sport else "?",
        league=league.key if league else "?",
        home_team=catalog.team_name(event.home_team_id),
        away_team=catalog.team_name(event.away_team_id),
        start_time=event.start_time,
        status=str(event.status),
        game_number=event.game_number,
    )


def selection_label(selection: Selection, catalog: Catalog) -> str:
    """Etiqueta legible de una selección: 'New York Yankees ML', 'Over 8.5'."""
    market = catalog.markets.get(selection.market_id)
    market_type = str(market.market_type) if market else "?"

    if market_type == "moneyline":
        return f"{catalog.team_name(selection.subject_id)} ML"
    if market_type == "spread":
        return f"{catalog.team_name(selection.subject_id)} {selection.line:+g}"
    if market_type == "total":
        return f"{str(selection.side).capitalize()} {selection.line:g}"
    return f"{catalog.team_name(selection.subject_id)} {selection.side!s} {selection.line:g}"


def serialize_candidate(
    candidate: Candidate, event: Event, selection: Selection, catalog: Catalog
) -> CandidateOut:
    market = catalog.markets.get(selection.market_id)
    best_decimal = candidate.best_price_decimal or 0.0
    return CandidateOut(
        id=candidate.id,
        event=serialize_event(event, catalog),
        selection_label=selection_label(selection, catalog),
        market=str(market.market_type) if market else "?",
        model_probability=candidate.model_prob,
        market_implied_probability=candidate.market_implied_prob,
        market_fair_probability=candidate.market_fair_prob,
        edge=candidate.edge,
        structural_edge=candidate.structural_edge or 0.0,
        # Se recalcula en vez de guardarse: es exactamente model - implícita del
        # mejor precio, y derivarla evita que un cambio en el pipeline deje una
        # columna desincronizada sin que nadie lo note.
        total_edge=candidate.model_prob - (1.0 / best_decimal if best_decimal > 1 else 0.0),
        expected_roi=candidate.expected_roi,
        best_price=PriceOut(
            sportsbook=catalog.book_name(candidate.best_sportsbook_id),
            american=candidate.best_price_american or decimal_to_american(best_decimal),
            decimal=best_decimal,
            captured_at=candidate.as_of,
        ),
        reference_book_count=candidate.reference_book_count,
        novig_method=candidate.novig_method,
        line_age_seconds=candidate.line_age_seconds,
        is_recommended=False,  # lo fija quien conozca las recomendaciones
        as_of=candidate.as_of,
    )


def serialize_recommendation(
    recommendation: Recommendation,
    candidate: CandidateOut,
    reasons: list[ReasonOut],
) -> RecommendationOut:
    return RecommendationOut(
        id=recommendation.id,
        candidate=candidate.model_copy(update={"is_recommended": True}),
        confidence_score=recommendation.confidence_score,
        confidence_version=recommendation.confidence_version,
        recommended_stake_units=recommendation.recommended_stake_units,
        sizing_method=recommendation.sizing_method,
        was_stake_capped=recommendation.was_stake_capped,
        filter_version=recommendation.filter_version or "?",
        correlation_group=recommendation.correlation_group,
        reasons=reasons,
        created_at=recommendation.created_at,
    )
