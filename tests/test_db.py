"""Esquema, invariantes de la base de datos y seeds."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import Engine, UniqueConstraint, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from sportstar.db.enums import BookType, MarketType, Period, Side, SubjectType
from sportstar.db.models import (
    NO_LINE,
    AppendOnlyViolation,
    Base,
    Event,
    EventFeature,
    FeatureSet,
    Market,
    OddsSnapshot,
    Selection,
)
from sportstar.seeds import seed_catalog

T0 = datetime(2026, 8, 19, 18, 0, tzinfo=UTC)


class TestSchema:
    def test_every_documented_table_exists(self, engine: Engine) -> None:
        assert len(Base.metadata.tables) == 29

    def test_no_unique_constraint_contains_a_nullable_column(self, engine: Engine) -> None:
        """Regresión estructural sobre toda la base, no solo sobre `selections`.

        En SQL `NULL != NULL`, así que una UNIQUE que incluya una columna nulable
        deja de restringir precisamente las filas donde esa columna es NULL. Es
        una trampa silenciosa: el esquema *parece* proteger contra duplicados y
        no lo hace, y el síntoma aparece mucho después como partidos contados dos
        veces en un backtest.

        Este test recorre todas las tablas para que el próximo `UniqueConstraint`
        que alguien añada no reintroduzca el problema.
        """
        offenders = [
            (table.name, constraint.name, column.name)
            for table in Base.metadata.tables.values()
            for constraint in table.constraints
            if isinstance(constraint, UniqueConstraint)
            for column in constraint.columns
            if column.nullable
        ]
        assert offenders == []

    def test_sqlite_enforces_foreign_keys(self, engine: Engine) -> None:
        # SQLite trae las FKs desactivadas por defecto; sin el PRAGMA serían
        # decorativas y las inconsistencias aparecerían meses después.
        with engine.connect() as conn:
            assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1

    def test_sqlite_uses_wal(self, engine: Engine) -> None:
        with engine.connect() as conn:
            assert conn.execute(text("PRAGMA journal_mode")).scalar() == "wal"


class TestSeeds:
    def test_seeds_the_full_catalog(self, session: Session) -> None:
        created = seed_catalog(session)
        assert created["sports"] == 6
        assert created["teams"] == 30  # MLB completo
        assert created["sportsbooks"] == 10

    def test_is_idempotent(self, session: Session) -> None:
        seed_catalog(session)
        session.commit()
        again = seed_catalog(session)
        assert all(count == 0 for count in again.values())

    def test_reference_and_executable_books_are_disjoint_sets(self, session: Session) -> None:
        """La separación de ARCHITECTURE.md §4.2 en los datos.

        Los sharp definen la probabilidad justa; los recreativos son donde se
        ejecuta. El edge del sistema vive en esa diferencia, así que ningún book
        debe hacer ambos papeles: sería compararse consigo mismo.
        """
        from sportstar.db.catalog import Sportsbook

        seed_catalog(session)
        books = session.query(Sportsbook).all()
        assert not [b for b in books if b.is_reference and b.is_executable]
        assert [b for b in books if b.is_reference]
        assert [b for b in books if b.is_executable]

    def test_reference_books_are_sharp(self, session: Session) -> None:
        from sportstar.db.catalog import Sportsbook

        seed_catalog(session)
        refs = session.query(Sportsbook).filter(Sportsbook.is_reference).all()
        assert all(b.book_type is BookType.SHARP for b in refs)


@pytest.fixture
def selection(session: Session) -> Selection:
    """Un evento con un mercado moneyline y su lado local."""
    from sportstar.db.catalog import League, Sport, Team

    seed_catalog(session)
    sport = session.query(Sport).filter_by(key="mlb").one()
    league = session.query(League).filter_by(key="mlb").one()
    home = session.query(Team).filter_by(key="NYY").one()
    away = session.query(Team).filter_by(key="BOS").one()

    event = Event(
        league_id=league.id,
        season=2026,
        event_date=date(2026, 8, 19),
        start_time=T0,
        home_team_id=home.id,
        away_team_id=away.id,
    )
    market = Market(sport_id=sport.id, market_type=MarketType.MONEYLINE, period=Period.GAME)
    session.add_all([event, market])
    session.flush()

    sel = Selection(
        event_id=event.id,
        market_id=market.id,
        subject_type=SubjectType.TEAM,
        subject_id=home.id,
        side=Side.HOME,
        line=NO_LINE,
    )
    session.add(sel)
    session.flush()
    return sel


class TestOddsAppendOnly:
    """La primera de las tres decisiones irreversibles, verificada en código."""

    def _snapshot(self, session: Session, selection: Selection, price: float) -> OddsSnapshot:
        from sportstar.db.catalog import Sportsbook

        book = session.query(Sportsbook).filter_by(key="pinnacle").one()
        snap = OddsSnapshot(
            selection_id=selection.id,
            sportsbook_id=book.id,
            price_american=price,
            price_decimal=1 + 100 / abs(price),
            line=NO_LINE,
            implied_prob=abs(price) / (abs(price) + 100),
            captured_at=T0,
        )
        session.add(snap)
        session.flush()
        return snap

    def test_update_is_blocked(self, session: Session, selection: Selection) -> None:
        snap = self._snapshot(session, selection, -110)
        snap.price_american = -120
        with pytest.raises(AppendOnlyViolation):
            session.flush()

    def test_delete_is_blocked(self, session: Session, selection: Selection) -> None:
        snap = self._snapshot(session, selection, -110)
        session.delete(snap)
        with pytest.raises(AppendOnlyViolation):
            session.flush()

    def test_a_price_change_is_a_new_row(self, session: Session, selection: Selection) -> None:
        # La forma correcta de reflejar un movimiento de línea.
        self._snapshot(session, selection, -110)
        self._snapshot(session, selection, -120)
        assert session.query(OddsSnapshot).count() == 2


class TestSelectionTaxonomy:
    def test_same_market_allows_different_lines(
        self, session: Session, selection: Selection
    ) -> None:
        # Alternate lines: la línea forma parte de la clave de la selección.
        session.add(
            Selection(
                event_id=selection.event_id,
                market_id=selection.market_id,
                subject_type=SubjectType.TEAM,
                subject_id=selection.subject_id,
                side=Side.HOME,
                line=-1.5,
            )
        )
        session.flush()
        assert session.query(Selection).count() == 2

    def test_duplicate_selection_is_rejected(self, session: Session, selection: Selection) -> None:
        session.add(
            Selection(
                event_id=selection.event_id,
                market_id=selection.market_id,
                subject_type=SubjectType.TEAM,
                subject_id=selection.subject_id,
                side=Side.HOME,
                line=NO_LINE,
            )
        )
        with pytest.raises(IntegrityError):
            session.flush()

    def test_player_props_need_no_schema_change(
        self, session: Session, selection: Selection
    ) -> None:
        """Phase 9 entra rellenando `subject_type='player'`, no migrando."""
        from sportstar.db.catalog import Player, Team

        team = session.query(Team).filter_by(key="NYY").one()
        player = Player(league_id=team.league_id, team_id=team.id, full_name="Test Pitcher")
        session.add(player)
        session.flush()

        sport_id = session.query(Market).filter_by(id=selection.market_id).one().sport_id
        prop_market = Market(
            sport_id=sport_id, market_type=MarketType.PLAYER_PROP, period=Period.GAME
        )
        session.add(prop_market)
        session.flush()

        session.add(
            Selection(
                event_id=selection.event_id,
                market_id=prop_market.id,
                subject_type=SubjectType.PLAYER,
                subject_id=player.id,
                side=Side.OVER,
                line=6.5,
            )
        )
        session.flush()  # sin errores: la taxonomía ya lo contemplaba


class TestFeatureAsOf:
    """La segunda decisión irreversible: `as_of` es parte de la clave."""

    def _feature_set(self, session: Session) -> FeatureSet:
        from sportstar.db.catalog import Sport

        sport = session.query(Sport).filter_by(key="mlb").one()
        fs = FeatureSet(sport_id=sport.id, name="mlb_v1", version="1", spec={})
        session.add(fs)
        session.flush()
        return fs

    def test_same_event_can_have_different_features_at_different_times(
        self, session: Session, selection: Selection
    ) -> None:
        # Las features de las 10:00 y las de las 18:00 son ambas correctas para
        # su momento. No se sobrescriben.
        from datetime import timedelta

        from sportstar.db.catalog import Team

        fs = self._feature_set(session)
        team = session.query(Team).filter_by(key="NYY").one()
        for offset, elo in ((0, 1500), (8, 1512)):
            session.add(
                EventFeature(
                    event_id=selection.event_id,
                    team_id=team.id,
                    feature_set_id=fs.id,
                    as_of=T0 - timedelta(hours=offset),
                    features={"elo": elo},
                    computed_at=T0,
                )
            )
        session.flush()
        assert session.query(EventFeature).count() == 2

    def test_duplicate_as_of_is_rejected(self, session: Session, selection: Selection) -> None:
        from sportstar.db.catalog import Team

        fs = self._feature_set(session)
        team = session.query(Team).filter_by(key="NYY").one()
        for _ in range(2):
            session.add(
                EventFeature(
                    event_id=selection.event_id,
                    team_id=team.id,
                    feature_set_id=fs.id,
                    as_of=T0,
                    features={},
                    computed_at=T0,
                )
            )
        with pytest.raises(IntegrityError):
            session.flush()
