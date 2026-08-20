"""Ingesta contra los dos payloads reales del 2026-08-20.

Es el test de extremo a extremo de Phase 2a: calendario real y odds reales
entrando a la base y saliendo como candidates.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from sportstar.data.normalizers import normalize_odds, normalize_schedule
from sportstar.db.catalog import League, Sport
from sportstar.db.enums import JobStatus
from sportstar.db.events import Event
from sportstar.db.markets import OddsSnapshot, Selection
from sportstar.db.ops import UnmatchedEntity
from sportstar.pipeline import ingest_odds, ingest_schedule
from sportstar.seeds import seed_catalog

FIXTURES = Path(__file__).parents[1] / "data" / "fixtures"
NOW = datetime(2026, 8, 20, 21, 57, tzinfo=UTC)


def load(name: str) -> object:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture
def catalog(session: Session) -> dict:
    seed_catalog(session)
    session.flush()
    return {
        "league": session.query(League).filter_by(key="mlb").one(),
        "sport": session.query(Sport).filter_by(key="mlb").one(),
    }


class TestScheduleIngest:
    def test_creates_every_game_of_a_real_slate(self, session: Session, catalog: dict) -> None:
        report = ingest_schedule(
            session,
            normalize_schedule(load("mlb_stats_api_schedule")),
            league_id=catalog["league"].id,
            run_id="r1",
        )
        assert report.status is JobStatus.SUCCESS
        assert report.get("received") == 9
        assert report.get("matched") == 9
        assert session.query(Event).count() == 9

    def test_leaves_no_unmatched_teams(self, session: Session, catalog: dict) -> None:
        ingest_schedule(
            session,
            normalize_schedule(load("mlb_stats_api_schedule")),
            league_id=catalog["league"].id,
            run_id="r1",
        )
        assert session.query(UnmatchedEntity).count() == 0

    def test_night_games_are_filed_under_the_previous_day(
        self, session: Session, catalog: dict
    ) -> None:
        """La jornada, no la fecha UTC.

        Dos de los nueve partidos empiezan pasada la medianoche UTC. Si se
        archivaran por el timestamp, "partidos de hoy" devolvería media jornada.
        """
        ingest_schedule(
            session,
            normalize_schedule(load("mlb_stats_api_schedule")),
            league_id=catalog["league"].id,
            run_id="r1",
        )
        dates = {e.event_date for e in session.query(Event).all()}
        assert len(dates) == 1
        crossing = [e for e in session.query(Event).all() if e.start_time.date() != e.event_date]
        assert len(crossing) == 2

    def test_is_idempotent(self, session: Session, catalog: dict) -> None:
        # Un sync que corre cada pocas horas no puede duplicar el slate.
        for _ in range(2):
            report = ingest_schedule(
                session,
                normalize_schedule(load("mlb_stats_api_schedule")),
                league_id=catalog["league"].id,
                run_id="r1",
            )
        assert session.query(Event).count() == 9
        assert report.get("updated") == 9
        assert report.get("created") == 0

    def test_records_final_scores(self, session: Session, catalog: dict) -> None:
        ingest_schedule(
            session,
            normalize_schedule(load("mlb_stats_api_schedule")),
            league_id=catalog["league"].id,
            run_id="r1",
        )
        finals = [e for e in session.query(Event).all() if e.home_score is not None]
        assert len(finals) == 6


class TestOddsIngest:
    def ingest(self, session: Session, catalog: dict):
        return ingest_odds(
            session,
            normalize_odds(load("the_odds_api_odds"), sport_key="mlb"),
            league_id=catalog["league"].id,
            sport_id=catalog["sport"].id,
            run_id="r1",
        )

    def test_matches_every_event_and_stores_every_price(
        self, session: Session, catalog: dict
    ) -> None:
        report = self.ingest(session, catalog)
        assert report.status is JobStatus.SUCCESS
        assert report.get("received") == 15
        assert report.get("matched") == 15
        assert report.get("snapshots") == 224
        assert session.query(OddsSnapshot).count() == 224

    def test_creates_two_selections_per_event(self, session: Session, catalog: dict) -> None:
        # Sin los dos lados no se puede quitar el vig.
        self.ingest(session, catalog)
        assert session.query(Selection).count() == 30

    def test_matches_against_the_existing_schedule_despite_clock_drift(
        self, session: Session, catalog: dict
    ) -> None:
        """Los proveedores no coinciden al minuto.

            MLB Stats API   2026-08-20T22:35:00Z
            The Odds API    2026-08-20T22:36:00Z

        Emparejar por timestamp exacto habría duplicado cada evento.
        """
        ingest_schedule(
            session,
            normalize_schedule(load("mlb_stats_api_schedule")),
            league_id=catalog["league"].id,
            run_id="r1",
        )
        before = session.query(Event).count()
        report = self.ingest(session, catalog)
        # Los 3 partidos que comparten ambos feeds se reutilizan; el resto son
        # jornadas siguientes que el calendario aún no tenía.
        assert report.get("created_from_odds") == 12
        assert session.query(Event).count() == before + 12

    def test_creates_events_the_schedule_has_not_seen_yet(
        self, session: Session, catalog: dict
    ) -> None:
        """El feed de odds va por delante del calendario.

        Crear esos eventos aquí evita perder sus precios de apertura, que son
        irrecuperables.
        """
        report = self.ingest(session, catalog)
        assert report.get("created_from_odds") == 15

    def test_leaves_no_unmatched_teams(self, session: Session, catalog: dict) -> None:
        self.ingest(session, catalog)
        assert session.query(UnmatchedEntity).count() == 0

    def test_appends_instead_of_overwriting(self, session: Session, catalog: dict) -> None:
        """La decisión irreversible del sistema, sobre datos reales.

        Un segundo sync del mismo mercado no pisa el precio anterior: lo añade.
        El precio de ayer no se puede reconstruir.
        """
        self.ingest(session, catalog)
        self.ingest(session, catalog)
        assert session.query(OddsSnapshot).count() == 448
        assert session.query(Selection).count() == 30

    def test_unknown_books_are_counted(self, session: Session, catalog: dict) -> None:
        from sportstar.db.catalog import Sportsbook

        session.query(Sportsbook).filter_by(key="bovada").delete()
        session.flush()
        report = self.ingest(session, catalog)
        assert report.get("unknown_books") > 0
