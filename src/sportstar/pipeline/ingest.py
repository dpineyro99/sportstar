"""Ingesta: estructuras normalizadas -> filas de la base.

Aquí vive el emparejamiento, que es donde se va la mitad del mantenimiento real
de un sistema como este. Lo que no empareja **no se descarta en silencio**: va a
`unmatched_entities` y se cuenta en el `JobReport`, cuya regla `matched == 0`
convierte un fallo silencioso en un job en estado FAILED.

Emparejar un evento entre proveedores no puede hacerse por timestamp exacto.
Medido sobre datos reales del mismo partido:

    MLB Stats API   2026-08-20T22:35:00Z
    The Odds API    2026-08-20T22:36:00Z

Un minuto de diferencia. Y la fecha UTC tampoco sirve como clave, porque los
partidos nocturnos cruzan medianoche y cada proveedor los fecha a su manera. Por
eso se empareja por **equipos + ventana temporal**.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.odds import american_to_decimal, decimal_to_implied
from ..data.normalizers.models import NormalizationResult, NormalizedEvent
from ..db.catalog import Sportsbook
from ..db.enums import EventStatus, MarketType, Period, Side, SubjectType
from ..db.events import Event
from ..db.markets import NO_LINE, Market, OddsSnapshot, Selection
from ..resolution import TeamResolver
from ..workers.reporting import MATCHED, RECEIVED, UNMATCHED, JobReport

# Tolerancia al emparejar eventos entre proveedores. Amplia a propósito: cubre
# el desfase de minutos entre feeds y los retrasos de inicio, y sigue siendo
# mucho menor que el hueco entre dos partidos del mismo enfrentamiento.
EVENT_MATCH_WINDOW = timedelta(hours=6)


def _find_event(
    session: Session, *, league_id: int, home_team_id: int, away_team_id: int, start_time: object
) -> Event | None:
    """Evento ya existente para el mismo enfrentamiento dentro de la ventana."""
    candidates = session.scalars(
        select(Event).where(
            Event.league_id == league_id,
            Event.home_team_id == home_team_id,
            Event.away_team_id == away_team_id,
        )
    )
    for event in candidates:
        if abs(event.start_time - start_time) <= EVENT_MATCH_WINDOW:  # type: ignore[operator]
            return event
    return None


def ingest_schedule(
    session: Session,
    result: NormalizationResult,
    *,
    league_id: int,
    run_id: str,
    provider: str = "mlb-stats-api",
) -> JobReport:
    """Crea o actualiza eventos desde un calendario normalizado."""
    report = JobReport(job_name="sync_schedule", run_id=run_id, sport_key="mlb")
    resolver = TeamResolver(session, league_id)

    for normalized in result.events:
        report.count(RECEIVED)
        home = resolver.resolve(
            normalized.home_team_raw,
            provider=provider,
            provider_id=normalized.provider_home_team_id,
        )
        away = resolver.resolve(
            normalized.away_team_raw,
            provider=provider,
            provider_id=normalized.provider_away_team_id,
        )
        if not (home.matched and away.matched):
            report.count(UNMATCHED)
            for raw, resolution in (
                (normalized.home_team_raw, home),
                (normalized.away_team_raw, away),
            ):
                if not resolution.matched:
                    resolver.record_unmatched(raw, provider=provider)
            continue

        assert home.entity_id is not None and away.entity_id is not None
        event = _find_event(
            session,
            league_id=league_id,
            home_team_id=home.entity_id,
            away_team_id=away.entity_id,
            start_time=normalized.start_time,
        )
        if event is None:
            event = Event(
                league_id=league_id,
                season=normalized.start_time.year,
                # La jornada del proveedor, no la fecha UTC del timestamp: los
                # partidos nocturnos cruzan medianoche y pertenecen al día
                # anterior.
                event_date=normalized.official_date or normalized.start_time.date(),
                start_time=normalized.start_time,
                home_team_id=home.entity_id,
                away_team_id=away.entity_id,
                game_number=normalized.game_number,
            )
            session.add(event)
            report.count("created")
        else:
            report.count("updated")

        if normalized.status:
            event.status = EventStatus(normalized.status)
        event.home_score = normalized.home_score
        event.away_score = normalized.away_score
        report.count(MATCHED)

    session.flush()
    for message in result.errors:
        report.error(message)
    return report.finish()


def ingest_odds(
    session: Session,
    result: NormalizationResult,
    *,
    league_id: int,
    sport_id: int,
    run_id: str,
    provider: str = "the-odds-api",
) -> JobReport:
    """Crea selecciones y **añade** snapshots de precio.

    Nunca sobrescribe un precio: cada observación es una fila nueva. Es la
    decisión irreversible del sistema — el precio de ayer no se puede
    reconstruir.
    """
    report = JobReport(job_name="sync_odds", run_id=run_id, sport_key="mlb")
    resolver = TeamResolver(session, league_id)
    books = {b.key: b for b in session.scalars(select(Sportsbook))}
    markets: dict[tuple[str, str], Market] = {}
    events_by_provider_id: dict[str, Event] = {}

    for normalized in result.events:
        report.count(RECEIVED)
        event = _match_event(session, normalized, resolver, league_id, provider, report)
        if event is not None:
            events_by_provider_id[normalized.provider_event_id] = event
            report.count(MATCHED)

    session.flush()

    for price in result.prices:
        event = events_by_provider_id.get(price.provider_event_id)
        if event is None:
            continue

        book = books.get(price.book_key)
        if book is None:
            # Un book nuevo en el feed es información: puede ser un sharp que
            # deberíamos estar usando como referencia.
            report.count("unknown_books")
            continue

        market = _get_market(session, markets, sport_id, price.market_type, price.period)
        selection = _get_selection(session, event, market, price, resolver, provider)
        if selection is None:
            report.count("unmatched_selections")
            continue

        decimal = american_to_decimal(price.price_american)
        session.add(
            OddsSnapshot(
                selection_id=selection.id,
                sportsbook_id=book.id,
                price_american=price.price_american,
                price_decimal=decimal,
                line=price.line if price.line is not None else NO_LINE,
                implied_prob=decimal_to_implied(decimal),
                captured_at=price.last_update,
                run_id=run_id,
            )
        )
        report.count("snapshots")

    session.flush()
    for message in result.errors:
        report.error(message)
    return report.finish()


def _match_event(
    session: Session,
    normalized: NormalizedEvent,
    resolver: TeamResolver,
    league_id: int,
    provider: str,
    report: JobReport,
) -> Event | None:
    home = resolver.resolve(normalized.home_team_raw, provider=provider)
    away = resolver.resolve(normalized.away_team_raw, provider=provider)
    if not (home.matched and away.matched):
        report.count(UNMATCHED)
        for raw, resolution in ((normalized.home_team_raw, home), (normalized.away_team_raw, away)):
            if not resolution.matched:
                resolver.record_unmatched(raw, provider=provider)
        return None

    assert home.entity_id is not None and away.entity_id is not None
    event = _find_event(
        session,
        league_id=league_id,
        home_team_id=home.entity_id,
        away_team_id=away.entity_id,
        start_time=normalized.start_time,
    )
    if event is None:
        # El feed de odds va por delante del calendario: trae partidos de días
        # siguientes que el sync de schedule todavía no ha creado. Crearlos aquí
        # evita perder sus precios de apertura, que son irrecuperables.
        event = Event(
            league_id=league_id,
            season=normalized.start_time.year,
            event_date=normalized.official_date or normalized.start_time.date(),
            start_time=normalized.start_time,
            home_team_id=home.entity_id,
            away_team_id=away.entity_id,
            game_number=normalized.game_number,
        )
        session.add(event)
        session.flush()
        report.count("created_from_odds")
    return event


def _get_market(
    session: Session,
    cache: dict[tuple[str, str], Market],
    sport_id: int,
    market_type: str,
    period: str,
) -> Market:
    key = (market_type, period)
    if key in cache:
        return cache[key]
    market = session.scalars(
        select(Market).where(
            Market.sport_id == sport_id,
            Market.market_type == MarketType(market_type),
            Market.period == Period(period),
        )
    ).first()
    if market is None:
        market = Market(
            sport_id=sport_id, market_type=MarketType(market_type), period=Period(period)
        )
        session.add(market)
        session.flush()
    cache[key] = market
    return market


def _get_selection(
    session: Session,
    event: Event,
    market: Market,
    price: object,
    resolver: TeamResolver,
    provider: str,
) -> Selection | None:
    """Encuentra o crea la selección que describe este precio.

    En moneyline el `side_raw` es el nombre del equipo, así que hay que
    resolverlo y decidir si es local o visitante comparándolo con el evento. Un
    fallo aquí colgaría el precio del lado contrario, que es de los errores que
    no se notan porque el número sigue pareciendo razonable.
    """
    side_raw = price.side_raw  # type: ignore[attr-defined]
    line = price.line if price.line is not None else NO_LINE  # type: ignore[attr-defined]

    if str(market.market_type) == "total":
        side = Side.OVER if side_raw.lower().startswith("over") else Side.UNDER
        subject_type, subject_id = SubjectType.EVENT, event.id
    else:
        resolution = resolver.resolve(side_raw, provider=provider)
        if not resolution.matched:
            resolver.record_unmatched(side_raw, provider=provider)
            return None
        if resolution.entity_id == event.home_team_id:
            side = Side.HOME
        elif resolution.entity_id == event.away_team_id:
            side = Side.AWAY
        else:
            # El equipo resuelve pero no juega este partido: emparejamiento
            # cruzado. Descartar es correcto; colgarlo sería peor.
            return None
        subject_type, subject_id = SubjectType.TEAM, resolution.entity_id

    existing = session.scalars(
        select(Selection).where(
            Selection.event_id == event.id,
            Selection.market_id == market.id,
            Selection.subject_type == subject_type,
            Selection.subject_id == subject_id,
            Selection.side == side,
            Selection.line == line,
        )
    ).first()
    if existing is not None:
        return existing

    selection = Selection(
        event_id=event.id,
        market_id=market.id,
        subject_type=subject_type,
        subject_id=subject_id,
        side=side,
        line=line,
    )
    session.add(selection)
    session.flush()
    return selection
