"""Persistencia del pipeline.

El test central es `TestFullRoundTrip`: carga precios de la base, ejecuta el
pipeline y vuelve a escribir, verificando que el linaje permite reconstruir la
apuesta. Es el ciclo que Phase 2a tiene que demostrar.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from sportstar.db.betting import Candidate, Recommendation, RecommendationReason
from sportstar.db.catalog import League, Sport, Sportsbook, Team
from sportstar.db.enums import MarketType, Period, ReasonSource, Side, SubjectType
from sportstar.db.markets import NO_LINE, Market, OddsSnapshot, Selection
from sportstar.db.modeling import Prediction
from sportstar.models import MarketConsensusModel, ensure_model_version
from sportstar.odds import (
    book_names,
    consensus_fair_probabilities,
    executable_book_ids,
    load_price_points,
    load_selection_ids,
    reference_book_ids,
)
from sportstar.pipeline import PersistenceError, evaluate_market, persist_evaluations
from sportstar.pipeline.persistence import persist_evaluation
from sportstar.seeds import seed_catalog

T0 = datetime(2026, 8, 19, 18, 0, tzinfo=UTC)
CAPTURED = T0 - timedelta(seconds=45)

# (book key, cuota local, cuota visitante). Los sharp coinciden; DraftKings paga
# de más por el local — el edge estructural que el pipeline debe encontrar.
PRICES = [
    ("pinnacle", 1.92, 1.98),
    ("circa", 1.93, 1.96),
    ("draftkings", 2.15, 1.75),
    ("fanduel", 2.05, 1.80),
]


@pytest.fixture
def market(session: Session) -> dict:
    """Un evento MLB con moneyline y precios reales en la base."""
    seed_catalog(session)
    session.flush()

    sport = session.query(Sport).filter_by(key="mlb").one()
    league = session.query(League).filter_by(key="mlb").one()
    home = session.query(Team).filter_by(key="NYY").one()
    away = session.query(Team).filter_by(key="BOS").one()

    from sportstar.db.events import Event

    event = Event(
        league_id=league.id,
        season=2026,
        event_date=date(2026, 8, 19),
        start_time=T0 + timedelta(hours=1),
        home_team_id=home.id,
        away_team_id=away.id,
    )
    market_row = Market(sport_id=sport.id, market_type=MarketType.MONEYLINE, period=Period.GAME)
    session.add_all([event, market_row])
    session.flush()

    selections = {}
    for team, side in ((home, Side.HOME), (away, Side.AWAY)):
        sel = Selection(
            event_id=event.id,
            market_id=market_row.id,
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
                    price_american=100.0,  # irrelevante para estos tests
                    price_decimal=price,
                    line=NO_LINE,
                    implied_prob=1 / price,
                    captured_at=CAPTURED,
                )
            )
    session.flush()

    return {
        "event": event,
        "market": market_row,
        "sport": sport,
        "selections": selections,
        "books": books,
    }


def run_pipeline(session: Session, market: dict):
    """Carga desde la base, evalúa y devuelve todo lo necesario para persistir."""
    event_id = market["event"].id
    market_id = market["market"].id

    points = load_price_points(session, event_id=event_id, market_id=market_id, as_of=T0)
    selections = load_selection_ids(session, event_id=event_id, market_id=market_id)
    consensus = consensus_fair_probabilities(
        points, selections, reference_book_ids(session), as_of=T0
    )
    assert consensus is not None
    predictions = MarketConsensusModel().predict(consensus, T0)
    evaluations = evaluate_market(
        selections=selections,
        consensus=consensus,
        predictions=predictions,
        points=points,
        executable_book_ids=executable_book_ids(session),
        as_of=T0,
    )
    return consensus, evaluations


class TestLoader:
    def test_loads_every_snapshot_with_its_id(self, session: Session, market: dict) -> None:
        points = load_price_points(
            session, event_id=market["event"].id, market_id=market["market"].id
        )
        assert len(points) == len(PRICES) * 2
        assert all(p.snapshot_id is not None for p in points)

    def test_as_of_excludes_later_snapshots(self, session: Session, market: dict) -> None:
        """La misma función sirve para producción y backtest.

        Mantener dos caminos de carga garantiza que se desincronicen y que el
        backtest deje de describir lo que el sistema haría de verdad.
        """
        points = load_price_points(
            session,
            event_id=market["event"].id,
            market_id=market["market"].id,
            as_of=CAPTURED - timedelta(seconds=1),
        )
        assert points == []

    def test_selection_order_is_stable(self, session: Session, market: dict) -> None:
        # `remove_vig` recibe una lista posicional: un orden que cambie entre
        # ejecuciones haría que el mismo mercado diera fair probabilities distintas.
        args = {"event_id": market["event"].id, "market_id": market["market"].id}
        assert load_selection_ids(session, **args) == load_selection_ids(session, **args)

    def test_reference_and_executable_books_come_from_the_database(
        self, session: Session, market: dict
    ) -> None:
        # Cuando cambie a qué books tienes acceso, cambia una fila, no el código.
        assert market["books"]["pinnacle"].id in reference_book_ids(session)
        assert market["books"]["draftkings"].id in executable_book_ids(session)
        assert not reference_book_ids(session) & executable_book_ids(session)


class TestFullRoundTrip:
    def test_database_to_pipeline_to_database(self, session: Session, market: dict) -> None:
        """El ciclo completo de Phase 2a."""
        consensus, evaluations = run_pipeline(session, market)
        model_version = ensure_model_version(
            session,
            name="market_consensus",
            version="v1",
            sport_id=market["sport"].id,
            market_type="moneyline",
            algorithm="sharp_consensus_novig",
        )

        result = persist_evaluations(
            session,
            evaluations,
            event_id=market["event"].id,
            model_version=model_version,
            consensus_snapshot_ids=consensus.contributing_snapshot_ids,
            book_names=book_names(session),
        )
        session.flush()

        assert result.counters == {"predictions": 2, "candidates": 2, "recommendations": 1}
        assert session.query(Candidate).count() == 2
        assert session.query(Recommendation).count() == 1

    def test_rejected_candidates_are_persisted_too(self, session: Session, market: dict) -> None:
        """Guardar los rechazados es lo que permite preguntar después
        "¿qué habría pasado con umbral 2% en vez de 3%?" sin volver a simular."""
        consensus, evaluations = run_pipeline(session, market)
        model_version = ensure_model_version(
            session,
            name="m",
            version="v1",
            sport_id=market["sport"].id,
            market_type="moneyline",
            algorithm="x",
        )
        persist_evaluations(
            session,
            evaluations,
            event_id=market["event"].id,
            model_version=model_version,
            consensus_snapshot_ids=consensus.contributing_snapshot_ids,
        )
        session.flush()

        rejected = [c for c in session.query(Candidate).all() if c.expected_value < 0]
        assert len(rejected) == 1
        assert session.query(Recommendation).filter_by(candidate_id=rejected[0].id).count() == 0

    def test_the_recommendation_comes_from_structural_edge(
        self, session: Session, market: dict
    ) -> None:
        consensus, evaluations = run_pipeline(session, market)
        model_version = ensure_model_version(
            session,
            name="m",
            version="v1",
            sport_id=market["sport"].id,
            market_type="moneyline",
            algorithm="x",
        )
        persist_evaluations(
            session,
            evaluations,
            event_id=market["event"].id,
            model_version=model_version,
            consensus_snapshot_ids=consensus.contributing_snapshot_ids,
        )
        session.flush()

        recommendation = session.query(Recommendation).one()
        candidate = session.query(Candidate).filter_by(id=recommendation.candidate_id).one()
        assert candidate.edge == pytest.approx(0.0, abs=1e-12)
        assert candidate.structural_edge > 0.02
        assert candidate.best_sportsbook_id == market["books"]["draftkings"].id


class TestLineage:
    def test_consensus_is_reconstructible_from_its_snapshots(
        self, session: Session, market: dict
    ) -> None:
        """Requisito del sistema: reconstruir cualquier apuesta histórica.

        El consenso sale de N books, así que una sola FK de referencia lo haría
        irreconstruible. Se guardan todos los snapshots que entraron en el
        promedio.
        """
        consensus, evaluations = run_pipeline(session, market)
        model_version = ensure_model_version(
            session,
            name="m",
            version="v1",
            sport_id=market["sport"].id,
            market_type="moneyline",
            algorithm="x",
        )
        persist_evaluations(
            session,
            evaluations,
            event_id=market["event"].id,
            model_version=model_version,
            consensus_snapshot_ids=consensus.contributing_snapshot_ids,
        )
        session.flush()

        candidate = session.query(Candidate).first()
        assert candidate is not None
        # Dos books de referencia x dos lados = cuatro snapshots.
        assert len(candidate.reference_odds_snapshot_ids) == 4
        assert candidate.reference_book_count == 2

        stored = (
            session.query(OddsSnapshot)
            .filter(OddsSnapshot.id.in_(candidate.reference_odds_snapshot_ids))
            .all()
        )
        sharp_ids = reference_book_ids(session)
        assert len(stored) == 4
        assert all(s.sportsbook_id in sharp_ids for s in stored)

    def test_prediction_links_candidate_to_its_model_version(
        self, session: Session, market: dict
    ) -> None:
        consensus, evaluations = run_pipeline(session, market)
        model_version = ensure_model_version(
            session,
            name="market_consensus",
            version="v1",
            sport_id=market["sport"].id,
            market_type="moneyline",
            algorithm="sharp_consensus_novig",
        )
        persist_evaluations(
            session,
            evaluations,
            event_id=market["event"].id,
            model_version=model_version,
            consensus_snapshot_ids=consensus.contributing_snapshot_ids,
        )
        session.flush()

        candidate = session.query(Candidate).first()
        assert candidate is not None
        prediction = session.query(Prediction).filter_by(id=candidate.prediction_id).one()
        assert prediction.model_version_id == model_version.id
        assert prediction.as_of == T0

    def test_novig_method_is_recorded(self, session: Session, market: dict) -> None:
        # Cambiar de método cambia todos los edges: sin saber cuál se usó no se
        # pueden comparar candidates de épocas distintas.
        consensus, evaluations = run_pipeline(session, market)
        model_version = ensure_model_version(
            session,
            name="m",
            version="v1",
            sport_id=market["sport"].id,
            market_type="moneyline",
            algorithm="x",
        )
        persist_evaluations(
            session,
            evaluations,
            event_id=market["event"].id,
            model_version=model_version,
            consensus_snapshot_ids=consensus.contributing_snapshot_ids,
        )
        session.flush()
        assert session.query(Candidate).first().novig_method == "proportional"


class TestReasons:
    def test_reasons_come_from_the_edge_decomposition(self, session: Session, market: dict) -> None:
        """Nunca se inventa un factor que el modelo no usó.

        `market_consensus_v1` solo sabe dos cosas, así que solo puede dar dos
        razones. Una razón inventada que suena bien es peor que ninguna:
        convierte una coincidencia en una convicción.
        """
        consensus, evaluations = run_pipeline(session, market)
        model_version = ensure_model_version(
            session,
            name="m",
            version="v1",
            sport_id=market["sport"].id,
            market_type="moneyline",
            algorithm="x",
        )
        persist_evaluations(
            session,
            evaluations,
            event_id=market["event"].id,
            model_version=model_version,
            consensus_snapshot_ids=consensus.contributing_snapshot_ids,
            book_names=book_names(session),
        )
        session.flush()

        reasons = session.query(RecommendationReason).order_by(RecommendationReason.rank).all()
        # El edge de modelo es exactamente 0 y no supera el mínimo: no se lista.
        assert len(reasons) == 1
        assert reasons[0].factor_key == "structural_edge"
        assert reasons[0].source is ReasonSource.MARKET
        assert reasons[0].contribution > 0.02
        assert "DraftKings" in reasons[0].factor_label

    def test_reason_contribution_matches_the_candidate(
        self, session: Session, market: dict
    ) -> None:
        consensus, evaluations = run_pipeline(session, market)
        model_version = ensure_model_version(
            session,
            name="m",
            version="v1",
            sport_id=market["sport"].id,
            market_type="moneyline",
            algorithm="x",
        )
        persist_evaluations(
            session,
            evaluations,
            event_id=market["event"].id,
            model_version=model_version,
            consensus_snapshot_ids=consensus.contributing_snapshot_ids,
        )
        session.flush()

        reason = session.query(RecommendationReason).one()
        recommendation = session.query(Recommendation).one()
        candidate = session.query(Candidate).filter_by(id=recommendation.candidate_id).one()
        assert reason.contribution == pytest.approx(candidate.structural_edge, abs=1e-12)


class TestCorrelationGroup:
    def test_selections_of_the_same_event_share_a_group(
        self, session: Session, market: dict
    ) -> None:
        """Agrupar de más limita exposición; agrupar de menos la multiplica sin
        que nadie se entere. La aproximación conservadora es la correcta hasta
        que exista el portfolio engine."""
        from sportstar.pipeline.persistence import correlation_group

        _, evaluations = run_pipeline(session, market)
        groups = {correlation_group(market["event"].id, e) for e in evaluations}
        assert len(groups) == 1


class TestRefusals:
    def test_refuses_to_persist_a_synthetic_price(self, session: Session, market: dict) -> None:
        """Un candidate cuyo precio no se puede señalar en `odds_snapshots` no es
        reconstruible. Romper aquí es mejor que descubrir el NULL meses después
        al auditar una apuesta."""
        from sportstar.odds import PricePoint

        _, evaluations = run_pipeline(session, market)
        evaluation = evaluations[0]
        synthetic = PricePoint(
            selection_id=evaluation.selection_id,
            sportsbook_id=evaluation.best_price.sportsbook_id,
            price_decimal=evaluation.best_price.price_decimal,
            captured_at=CAPTURED,
        )
        # `dataclasses.replace`: CandidateEvaluation es frozen con slots, no tiene __dict__.
        broken = replace(evaluation, best_price=synthetic)
        model_version = ensure_model_version(
            session,
            name="m",
            version="v1",
            sport_id=market["sport"].id,
            market_type="moneyline",
            algorithm="x",
        )

        with pytest.raises(PersistenceError, match="snapshot_id"):
            persist_evaluation(
                session,
                broken,
                event_id=market["event"].id,
                model_version=model_version,
                consensus_snapshot_ids=(),
            )


class TestModelRegistry:
    def test_is_idempotent(self, session: Session, market: dict) -> None:
        args = {
            "name": "market_consensus",
            "version": "v1",
            "sport_id": market["sport"].id,
            "market_type": "moneyline",
            "algorithm": "sharp_consensus_novig",
        }
        first = ensure_model_version(session, **args)
        second = ensure_model_version(session, **args)
        assert first.id == second.id

    def test_different_versions_are_different_rows(self, session: Session, market: dict) -> None:
        """Una versión de modelo es inmutable.

        Si cambia algo del modelo cambia la versión; si no, las predicciones
        antiguas quedarían atribuidas a algo que ya no es lo que las generó.
        """
        base = {
            "name": "market_consensus",
            "sport_id": market["sport"].id,
            "market_type": "moneyline",
            "algorithm": "x",
        }
        v1 = ensure_model_version(session, version="v1", **base)
        v2 = ensure_model_version(session, version="v2", **base)
        assert v1.id != v2.id

    def test_active_model_lookup(self, session: Session, market: dict) -> None:
        from sportstar.models import active_model

        assert active_model(session, sport_id=market["sport"].id, market_type="moneyline") is None
        ensure_model_version(
            session,
            name="m",
            version="v1",
            sport_id=market["sport"].id,
            market_type="moneyline",
            algorithm="x",
            is_active=True,
        )
        found = active_model(session, sport_id=market["sport"].id, market_type="moneyline")
        assert found is not None and found.name == "m"
