"""Fixtures de la API: una base poblada con un mercado real y su recomendación."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from sportstar.api.app import app
from sportstar.api.deps import get_session
from sportstar.db.catalog import League, Sport, Sportsbook, Team
from sportstar.db.enums import MarketType, Period, Side, SubjectType
from sportstar.db.events import Event
from sportstar.db.markets import NO_LINE, Market, OddsSnapshot, Selection
from sportstar.models import MarketConsensusModel, ensure_model_version
from sportstar.odds import (
    book_names,
    consensus_fair_probabilities,
    executable_book_ids,
    load_price_points,
    load_selection_ids,
    reference_book_ids,
)
from sportstar.pipeline import evaluate_market, persist_evaluations
from sportstar.seeds import seed_catalog

NOW = datetime(2026, 8, 19, 18, 0, tzinfo=UTC)
CAPTURED = NOW - timedelta(seconds=45)

PRICES = [
    ("betonlineag", 1.92, 1.98),
    ("betus", 1.93, 1.96),
    ("draftkings", 2.15, 1.75),
    ("fanduel", 2.05, 1.80),
]


@pytest.fixture
def populated(session: Session) -> Session:
    """Un evento MLB con precios, candidates y una recomendación persistida."""
    seed_catalog(session)
    session.flush()

    sport = session.query(Sport).filter_by(key="mlb").one()
    league = session.query(League).filter_by(key="mlb").one()
    home = session.query(Team).filter_by(key="NYY").one()
    away = session.query(Team).filter_by(key="BOS").one()

    event = Event(
        league_id=league.id,
        season=2026,
        event_date=date(2026, 8, 19),
        start_time=NOW + timedelta(hours=1),
        home_team_id=home.id,
        away_team_id=away.id,
    )
    market = Market(sport_id=sport.id, market_type=MarketType.MONEYLINE, period=Period.GAME)
    session.add_all([event, market])
    session.flush()

    selections = {}
    for team, side in ((home, Side.HOME), (away, Side.AWAY)):
        sel = Selection(
            event_id=event.id,
            market_id=market.id,
            subject_type=SubjectType.TEAM,
            subject_id=team.id,
            side=side,
            line=NO_LINE,
        )
        session.add(sel)
        selections[side] = sel
    session.flush()

    books = {b.key: b for b in session.query(Sportsbook).all()}
    for book_key, home_price, away_price in PRICES:
        for side, price in ((Side.HOME, home_price), (Side.AWAY, away_price)):
            session.add(
                OddsSnapshot(
                    selection_id=selections[side].id,
                    sportsbook_id=books[book_key].id,
                    price_american=-110,
                    price_decimal=price,
                    line=NO_LINE,
                    implied_prob=1 / price,
                    captured_at=CAPTURED,
                )
            )
    session.flush()

    points = load_price_points(session, event_id=event.id, market_id=market.id, as_of=NOW)
    selection_ids = load_selection_ids(session, event_id=event.id, market_id=market.id)
    consensus = consensus_fair_probabilities(
        points, selection_ids, reference_book_ids(session), as_of=NOW
    )
    assert consensus is not None
    evaluations = evaluate_market(
        selections=selection_ids,
        consensus=consensus,
        predictions=MarketConsensusModel().predict(consensus, NOW),
        points=points,
        executable_book_ids=executable_book_ids(session),
        as_of=NOW,
    )
    model_version = ensure_model_version(
        session,
        name="market_consensus",
        version="v1",
        sport_id=sport.id,
        market_type="moneyline",
        algorithm="sharp_consensus_novig",
        is_active=True,
    )
    persist_evaluations(
        session,
        evaluations,
        event_id=event.id,
        model_version=model_version,
        consensus_snapshot_ids=consensus.contributing_snapshot_ids,
        book_names=book_names(session),
    )
    session.commit()
    return session


@pytest.fixture
def client(engine: Engine, populated: Session) -> Iterator[TestClient]:
    def override() -> Iterator[Session]:
        yield populated

    app.dependency_overrides[get_session] = override
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
