"""Checks de Data Health.

Cada test construye el escenario roto que el check debe atrapar **y** su
contraparte sana que no debe disparar. Un check que salta siempre entrena a
cualquiera a ignorar el panel, que es como muere un sistema de alertas.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from sportstar.db.catalog import League, Sport, Sportsbook, Team
from sportstar.db.enums import (
    EventStatus,
    JobStatus,
    MarketType,
    Period,
    Severity,
    Side,
    SubjectType,
)
from sportstar.db.events import Event
from sportstar.db.markets import NO_LINE, Market, OddsSnapshot, Selection
from sportstar.db.ops import DataHealthCheck, JobRun, UnmatchedEntity
from sportstar.health import persist_report, run_checks
from sportstar.health.checks import (
    check_closing_coverage,
    check_closing_lines_missing,
    check_events_without_odds,
    check_failed_jobs,
    check_impossible_probabilities,
    check_odds_after_start,
    check_stale_odds,
    check_unmatched_backlog,
)
from sportstar.seeds import seed_catalog

NOW = datetime(2026, 8, 19, 18, 0, tzinfo=UTC)


@pytest.fixture
def world(session: Session) -> dict:
    seed_catalog(session)
    session.flush()
    sport = session.query(Sport).filter_by(key="mlb").one()
    market = Market(sport_id=sport.id, market_type=MarketType.MONEYLINE, period=Period.GAME)
    session.add(market)
    session.flush()
    return {
        "league": session.query(League).filter_by(key="mlb").one(),
        "market": market,
        "book": session.query(Sportsbook).filter_by(key="pinnacle").one(),
        "teams": {t.key: t for t in session.query(Team).all()},
    }


def make_event(
    session: Session, world: dict, *, start: datetime, status: EventStatus, game_number: int = 1
) -> Event:
    event = Event(
        league_id=world["league"].id,
        season=2026,
        event_date=start.date(),
        start_time=start,
        home_team_id=world["teams"]["NYY"].id,
        away_team_id=world["teams"]["BOS"].id,
        status=status,
        game_number=game_number,
    )
    session.add(event)
    session.flush()
    return event


def add_price(
    session: Session,
    world: dict,
    event: Event,
    *,
    captured_at: datetime,
    decimal: float = 1.91,
    implied: float = 0.5238,
) -> OddsSnapshot:
    # Se reutiliza la selección si ya existe: en producción los precios se
    # acumulan sobre la misma selección (tabla append-only), no crean una nueva.
    selection = (
        session.query(Selection)
        .filter_by(event_id=event.id, market_id=world["market"].id, side=Side.HOME)
        .one_or_none()
    )
    if selection is None:
        selection = Selection(
            event_id=event.id,
            market_id=world["market"].id,
            subject_type=SubjectType.TEAM,
            subject_id=world["teams"]["NYY"].id,
            side=Side.HOME,
            line=NO_LINE,
        )
        session.add(selection)
        session.flush()
    snapshot = OddsSnapshot(
        selection_id=selection.id,
        sportsbook_id=world["book"].id,
        price_american=-110,
        price_decimal=decimal,
        line=NO_LINE,
        implied_prob=implied,
        captured_at=captured_at,
    )
    session.add(snapshot)
    session.flush()
    return snapshot


class TestEventsWithoutOdds:
    def test_flags_an_imminent_event_with_no_prices(self, session: Session, world: dict) -> None:
        make_event(session, world, start=NOW + timedelta(hours=2), status=EventStatus.SCHEDULED)
        findings = check_events_without_odds(session, now=NOW)
        assert len(findings) == 1
        assert findings[0].severity is Severity.WARNING

    def test_stays_quiet_when_prices_exist(self, session: Session, world: dict) -> None:
        event = make_event(
            session, world, start=NOW + timedelta(hours=2), status=EventStatus.SCHEDULED
        )
        add_price(session, world, event, captured_at=NOW)
        assert check_events_without_odds(session, now=NOW) == []

    def test_ignores_events_far_in_the_future(self, session: Session, world: dict) -> None:
        # Que un partido de dentro de tres días no tenga precios es normal.
        make_event(session, world, start=NOW + timedelta(days=3), status=EventStatus.SCHEDULED)
        assert check_events_without_odds(session, now=NOW) == []


class TestStaleOdds:
    def test_flags_an_imminent_event_whose_latest_price_is_old(
        self, session: Session, world: dict
    ) -> None:
        """Que el precio esté viejo no lo arregla el filtro: significa que el
        sync se paró, y eso es un problema de infraestructura, no de selección."""
        event = make_event(
            session, world, start=NOW + timedelta(hours=1), status=EventStatus.SCHEDULED
        )
        add_price(session, world, event, captured_at=NOW - timedelta(minutes=45))
        findings = check_stale_odds(session, now=NOW)
        assert len(findings) == 1
        assert findings[0].severity is Severity.CRITICAL

    def test_stays_quiet_with_fresh_prices(self, session: Session, world: dict) -> None:
        event = make_event(
            session, world, start=NOW + timedelta(hours=1), status=EventStatus.SCHEDULED
        )
        add_price(session, world, event, captured_at=NOW - timedelta(minutes=2))
        assert check_stale_odds(session, now=NOW) == []

    def test_only_the_latest_price_counts(self, session: Session, world: dict) -> None:
        # Un precio viejo no es un problema si hay uno reciente detrás: la tabla
        # es append-only, así que el histórico siempre contiene precios antiguos.
        event = make_event(
            session, world, start=NOW + timedelta(hours=1), status=EventStatus.SCHEDULED
        )
        add_price(session, world, event, captured_at=NOW - timedelta(hours=3))
        add_price(session, world, event, captured_at=NOW - timedelta(minutes=1))
        assert check_stale_odds(session, now=NOW) == []


class TestClosingLines:
    def test_flags_a_started_event_with_no_pregame_price(
        self, session: Session, world: dict
    ) -> None:
        """El único fallo irrecuperable del sistema.

        Sin cierre no hay CLV, y sin CLV la validación pierde la muestra que hace
        viable evaluar un modelo en semanas en vez de en temporadas.
        """
        make_event(session, world, start=NOW - timedelta(hours=2), status=EventStatus.FINAL)
        findings = check_closing_lines_missing(session, now=NOW)
        assert len(findings) == 1
        assert findings[0].severity is Severity.CRITICAL
        assert "irrecuperable" in findings[0].message

    def test_stays_quiet_when_a_pregame_price_exists(self, session: Session, world: dict) -> None:
        start = NOW - timedelta(hours=2)
        event = make_event(session, world, start=start, status=EventStatus.FINAL)
        add_price(session, world, event, captured_at=start - timedelta(minutes=5))
        assert check_closing_lines_missing(session, now=NOW) == []

    def test_an_in_play_price_does_not_count_as_a_close(
        self, session: Session, world: dict
    ) -> None:
        # Un precio capturado tras el primer lanzamiento refleja lo que pasa en
        # el campo, no la estimación final del mercado.
        start = NOW - timedelta(hours=2)
        event = make_event(session, world, start=start, status=EventStatus.FINAL)
        add_price(session, world, event, captured_at=start + timedelta(minutes=10))
        assert len(check_closing_lines_missing(session, now=NOW)) == 1

    def test_scheduled_events_are_not_flagged(self, session: Session, world: dict) -> None:
        make_event(session, world, start=NOW + timedelta(hours=2), status=EventStatus.SCHEDULED)
        assert check_closing_lines_missing(session, now=NOW) == []


class TestClosingCoverage:
    def test_flags_coverage_below_the_threshold(self, session: Session, world: dict) -> None:
        start = NOW - timedelta(hours=3)
        covered = make_event(session, world, start=start, status=EventStatus.FINAL, game_number=1)
        add_price(session, world, covered, captured_at=start - timedelta(minutes=5))
        make_event(session, world, start=start, status=EventStatus.FINAL, game_number=2)

        findings = check_closing_coverage(session, now=NOW)
        assert len(findings) == 1
        assert findings[0].severity is Severity.CRITICAL
        assert "50.0%" in findings[0].message

    def test_full_coverage_is_quiet(self, session: Session, world: dict) -> None:
        start = NOW - timedelta(hours=3)
        event = make_event(session, world, start=start, status=EventStatus.FINAL)
        add_price(session, world, event, captured_at=start - timedelta(minutes=5))
        assert check_closing_coverage(session, now=NOW) == []

    def test_no_finished_events_is_quiet(self, session: Session, world: dict) -> None:
        assert check_closing_coverage(session, now=NOW) == []


class TestImpossibleProbabilities:
    @pytest.mark.parametrize(
        ("decimal", "implied"), [(1.91, 0.0), (1.91, 1.0), (1.91, 1.5), (1.0, 0.5)]
    )
    def test_flags_corrupt_prices(
        self, session: Session, world: dict, decimal: float, implied: float
    ) -> None:
        event = make_event(session, world, start=NOW, status=EventStatus.SCHEDULED)
        add_price(session, world, event, captured_at=NOW, decimal=decimal, implied=implied)
        findings = check_impossible_probabilities(session)
        assert len(findings) == 1
        assert findings[0].severity is Severity.CRITICAL

    def test_normal_prices_are_quiet(self, session: Session, world: dict) -> None:
        event = make_event(session, world, start=NOW, status=EventStatus.SCHEDULED)
        add_price(session, world, event, captured_at=NOW)
        assert check_impossible_probabilities(session) == []


class TestOddsAfterStart:
    def test_reports_in_play_prices_as_informational(self, session: Session, world: dict) -> None:
        """El in-play existe y es legítimo; el problema sería usarlo como pregame.

        Por eso es INFO: marcarlo como error entrenaría a ignorar el panel.
        """
        start = NOW - timedelta(hours=1)
        event = make_event(session, world, start=start, status=EventStatus.LIVE)
        add_price(session, world, event, captured_at=start + timedelta(minutes=20))
        findings = check_odds_after_start(session)
        assert len(findings) == 1
        assert findings[0].severity is Severity.INFO

    def test_pregame_prices_are_quiet(self, session: Session, world: dict) -> None:
        start = NOW + timedelta(hours=1)
        event = make_event(session, world, start=start, status=EventStatus.SCHEDULED)
        add_price(session, world, event, captured_at=NOW)
        assert check_odds_after_start(session) == []


class TestUnmatchedBacklog:
    def test_flags_a_growing_queue(self, session: Session) -> None:
        from sportstar.db.enums import EntityType

        for i in range(12):
            session.add(
                UnmatchedEntity(
                    provider="odds-api",
                    entity_type=EntityType.TEAM,
                    raw_value=f"equipo {i}",
                    first_seen_at=NOW,
                    last_seen_at=NOW,
                    occurrences=i + 1,
                )
            )
        session.flush()
        findings = check_unmatched_backlog(session)
        assert len(findings) == 1
        # Prioriza por frecuencia: lo que más datos nos ha costado.
        assert "equipo 11" in findings[0].message

    def test_a_small_queue_is_quiet(self, session: Session) -> None:
        assert check_unmatched_backlog(session) == []


class TestFailedJobs:
    def test_flags_recent_failures(self, session: Session) -> None:
        session.add(
            JobRun(
                job_name="sync_odds",
                sport_key="mlb",
                run_id="r1",
                started_at=NOW - timedelta(hours=1),
                status=JobStatus.FAILED,
                error_summary="se recibieron 84 eventos y no se emparejó ninguno",
            )
        )
        session.flush()
        findings = check_failed_jobs(session, now=NOW)
        assert len(findings) == 1
        assert "no se emparejó ninguno" in findings[0].message

    def test_ignores_old_failures(self, session: Session) -> None:
        session.add(
            JobRun(
                job_name="sync_odds",
                sport_key="mlb",
                run_id="r1",
                started_at=NOW - timedelta(days=3),
                status=JobStatus.FAILED,
            )
        )
        session.flush()
        assert check_failed_jobs(session, now=NOW) == []

    def test_successful_jobs_are_quiet(self, session: Session) -> None:
        session.add(
            JobRun(
                job_name="sync_odds",
                sport_key="mlb",
                run_id="r1",
                started_at=NOW - timedelta(hours=1),
                status=JobStatus.SUCCESS,
            )
        )
        session.flush()
        assert check_failed_jobs(session, now=NOW) == []


class TestRunner:
    def test_a_clean_database_is_healthy(self, session: Session, world: dict) -> None:
        report = run_checks(session, now=NOW)
        assert report.is_healthy
        assert "ok" in report.render()

    def test_a_critical_finding_breaks_health(self, session: Session, world: dict) -> None:
        make_event(session, world, start=NOW - timedelta(hours=2), status=EventStatus.FINAL)
        report = run_checks(session, now=NOW)
        assert not report.is_healthy
        assert report.critical

    def test_warnings_alone_do_not_break_health(self, session: Session, world: dict) -> None:
        """Un WARNING permanente que marcase el sistema como enfermo entrenaría a
        cualquiera a ignorar el indicador."""
        make_event(session, world, start=NOW + timedelta(hours=2), status=EventStatus.SCHEDULED)
        report = run_checks(session, now=NOW)
        assert report.by_severity(Severity.WARNING)
        assert report.is_healthy

    def test_render_orders_by_severity(self, session: Session, world: dict) -> None:
        make_event(session, world, start=NOW - timedelta(hours=2), status=EventStatus.FINAL)
        make_event(
            session,
            world,
            start=NOW + timedelta(hours=2),
            status=EventStatus.SCHEDULED,
            game_number=2,
        )
        rendered = run_checks(session, now=NOW).render()
        assert rendered.index("CRITICAL") < rendered.index("WARNING")


class TestPersistence:
    def test_findings_are_written_once(self, session: Session, world: dict) -> None:
        """Un hallazgo que sigue apareciendo no se duplica: conserva su fila y su
        `detected_at`, que es lo que permite responder "¿desde cuándo?"."""
        make_event(session, world, start=NOW - timedelta(hours=2), status=EventStatus.FINAL)

        created, resolved = persist_report(session, run_checks(session, now=NOW))
        assert created >= 1 and resolved == 0
        first = session.query(DataHealthCheck).first()
        assert first is not None
        detected_at = first.detected_at

        later = NOW + timedelta(hours=1)
        created_again, _ = persist_report(session, run_checks(session, now=later))
        assert created_again == 0
        session.expire_all()
        assert session.query(DataHealthCheck).first().detected_at == detected_at

    def test_disappearing_findings_are_resolved(self, session: Session, world: dict) -> None:
        # Sin esto el panel se llena de ruido histórico y deja de mirarse.
        start = NOW - timedelta(hours=2)
        event = make_event(session, world, start=start, status=EventStatus.FINAL)
        persist_report(session, run_checks(session, now=NOW))
        assert (
            session.query(DataHealthCheck).filter(DataHealthCheck.resolved_at.is_(None)).count()
            >= 1
        )

        add_price(session, world, event, captured_at=start - timedelta(minutes=5))
        _, resolved = persist_report(session, run_checks(session, now=NOW))

        assert resolved >= 1
        assert (
            session.query(DataHealthCheck).filter(DataHealthCheck.resolved_at.is_(None)).count()
            == 0
        )
