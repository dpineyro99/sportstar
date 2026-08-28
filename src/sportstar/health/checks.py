"""Checks de calidad de datos sobre la base.

Estos checks existen porque **el modo de fallo peligroso de este sistema no es
el error, es el silencio**. Un pipeline que sigue corriendo con datos de ayer no
lanza excepciones: produce recomendaciones plausibles sobre precios que ya no
existen, y el backtest posterior las valida encantado.

Cada check devuelve `Finding`s con severidad. La regla de severidad:

- `CRITICAL` — invalida decisiones que se están tomando ahora, o pierde datos de
  forma irrecuperable. Exige intervención.
- `WARNING`  — degrada la calidad pero el sistema sigue siendo utilizable.
- `INFO`     — vale la pena mirarlo, no urge.

`closing_lines_missing` es CRITICAL aunque no rompa nada hoy: es el único fallo
del sistema cuya ventana no vuelve. El precio de cierre de ayer se perdió ayer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.enums import EventStatus, JobStatus, Severity
from ..db.events import Event
from ..db.markets import OddsSnapshot, Selection
from ..db.ops import JobRun, UnmatchedEntity

# Un precio de más de 15 minutos ya no describe el mercado: los gates lo cortan
# a los 10, así que superar 15 significa que el sync no está corriendo.
STALE_ODDS_MINUTES = 15
# Ventana antes del inicio en la que ya deberíamos tener precios de un partido.
PREGAME_WINDOW_HOURS = 6
MIN_CLOSING_COVERAGE = 0.95
UNMATCHED_BACKLOG_WARNING = 10


@dataclass(frozen=True, slots=True)
class Finding:
    """Un problema detectado. `entity_id` permite navegar hasta él desde el panel."""

    check_name: str
    severity: Severity
    message: str
    entity_type: str | None = None
    entity_id: int | None = None


def check_events_without_odds(session: Session, *, now: datetime | None = None) -> list[Finding]:
    """Partidos que empiezan pronto y sobre los que no tenemos ni un precio.

    No es lo mismo que "no hay valor": es que no podemos ni mirar. Si se repite
    sobre muchos eventos, el sync de odds no está emparejando.
    """
    moment = now or datetime.now(UTC)
    horizon = moment + timedelta(hours=PREGAME_WINDOW_HOURS)

    priced = select(Selection.event_id).join(
        OddsSnapshot, OddsSnapshot.selection_id == Selection.id
    )
    query = select(Event).where(
        Event.status == EventStatus.SCHEDULED,
        Event.start_time > moment,
        Event.start_time <= horizon,
        Event.id.not_in(priced),
    )
    return [
        Finding(
            "events_without_odds",
            Severity.WARNING,
            f"El evento {event.id} empieza en menos de {PREGAME_WINDOW_HOURS}h y no "
            "tiene ningún precio registrado.",
            entity_type="event",
            entity_id=event.id,
        )
        for event in session.scalars(query)
    ]


def check_stale_odds(session: Session, *, now: datetime | None = None) -> list[Finding]:
    """Partidos inminentes cuyo precio más reciente ya es viejo.

    Un edge calculado sobre un precio obsoleto es un artefacto: el precio ya no
    está ahí. Los gates lo rechazan, pero que ocurra significa que el sync se
    paró — y eso no lo arregla el filtro.
    """
    moment = now or datetime.now(UTC)
    cutoff = moment - timedelta(minutes=STALE_ODDS_MINUTES)

    query = (
        select(Event.id, func.max(OddsSnapshot.captured_at).label("latest"))
        .join(Selection, Selection.event_id == Event.id)
        .join(OddsSnapshot, OddsSnapshot.selection_id == Selection.id)
        .where(
            Event.status == EventStatus.SCHEDULED,
            Event.start_time > moment,
            Event.start_time <= moment + timedelta(hours=PREGAME_WINDOW_HOURS),
        )
        .group_by(Event.id)
        .having(func.max(OddsSnapshot.captured_at) < cutoff)
    )
    return [
        Finding(
            "stale_odds",
            Severity.CRITICAL,
            f"El evento {event_id} empieza pronto y su precio más reciente es de "
            f"{latest}. El sync de odds no está corriendo.",
            entity_type="event",
            entity_id=event_id,
        )
        for event_id, latest in session.execute(query)
    ]


def check_closing_lines_missing(session: Session, *, now: datetime | None = None) -> list[Finding]:
    """Partidos ya empezados sin ningún precio anterior a su inicio.

    **El único fallo irrecuperable del sistema.** Sin cierre no hay CLV, y sin
    CLV la validación de §4.6 pierde su muestra — que es precisamente lo que hace
    viable evaluar un modelo en semanas en vez de en temporadas. La ventana no
    vuelve: por eso es CRITICAL aunque hoy no rompa nada.
    """
    moment = now or datetime.now(UTC)

    with_closing = (
        select(Selection.event_id)
        .join(OddsSnapshot, OddsSnapshot.selection_id == Selection.id)
        .join(Event, Event.id == Selection.event_id)
        .where(OddsSnapshot.captured_at < Event.start_time)
    )
    query = select(Event).where(
        Event.start_time <= moment,
        Event.status.in_([EventStatus.LIVE, EventStatus.FINAL]),
        Event.id.not_in(with_closing),
    )
    return [
        Finding(
            "closing_lines_missing",
            Severity.CRITICAL,
            f"El evento {event.id} empezó sin ningún precio capturado antes del "
            "inicio. Sin cierre no hay CLV, y la muestra perdida es irrecuperable.",
            entity_type="event",
            entity_id=event.id,
        )
        for event in session.scalars(query)
    ]


def check_odds_after_start(session: Session) -> list[Finding]:
    """Precios capturados después del inicio del evento.

    No es un error por sí solo —el in-play existe— pero sí lo es si alguien los
    usa como pregame: reflejan lo que ya está pasando en el campo y producen edge
    fantasma enorme y muy convincente.
    """
    query = (
        select(OddsSnapshot.id, Selection.event_id)
        .join(Selection, Selection.id == OddsSnapshot.selection_id)
        .join(Event, Event.id == Selection.event_id)
        .where(OddsSnapshot.captured_at >= Event.start_time)
    )
    rows = list(session.execute(query))
    if not rows:
        return []
    return [
        Finding(
            "odds_after_start",
            Severity.INFO,
            f"{len(rows)} precio(s) capturados en o después del inicio de su evento. "
            "Válidos como in-play; nunca deben tratarse como pregame.",
        )
    ]


def check_impossible_probabilities(session: Session) -> list[Finding]:
    """Probabilidades implícitas fuera de (0, 1) o cuotas imposibles.

    Un precio corrupto se propaga en silencio hasta el edge. Aquí se ve.
    """
    query = select(OddsSnapshot).where(
        (OddsSnapshot.implied_prob <= 0)
        | (OddsSnapshot.implied_prob >= 1)
        | (OddsSnapshot.price_decimal <= 1.0)
    )
    return [
        Finding(
            "impossible_probability",
            Severity.CRITICAL,
            f"Snapshot {row.id}: implied_prob={row.implied_prob}, "
            f"decimal={row.price_decimal}. Precio corrupto.",
            entity_type="odds_snapshot",
            entity_id=row.id,
        )
        for row in session.scalars(query)
    ]


def check_closing_coverage(session: Session, *, now: datetime | None = None) -> list[Finding]:
    """Qué fracción de los partidos ya empezados tiene cierre capturado.

    Por debajo del umbral la validación no solo pierde potencia: se **sesga**,
    porque los eventos capturados no son una muestra aleatoria de los que hubo.
    """
    moment = now or datetime.now(UTC)

    total = session.scalar(
        select(func.count(Event.id)).where(
            Event.start_time <= moment,
            Event.status.in_([EventStatus.LIVE, EventStatus.FINAL]),
        )
    )
    if not total:
        return []

    with_closing = (
        session.scalar(
            select(func.count(func.distinct(Selection.event_id)))
            .select_from(Selection)
            .join(OddsSnapshot, OddsSnapshot.selection_id == Selection.id)
            .join(Event, Event.id == Selection.event_id)
            .where(
                OddsSnapshot.captured_at < Event.start_time,
                Event.start_time <= moment,
                Event.status.in_([EventStatus.LIVE, EventStatus.FINAL]),
            )
        )
        or 0
    )

    coverage = with_closing / total
    if coverage >= MIN_CLOSING_COVERAGE:
        return []
    return [
        Finding(
            "closing_coverage",
            Severity.CRITICAL,
            f"Cobertura de cierres {coverage:.1%} ({with_closing}/{total}), por debajo "
            f"del {MIN_CLOSING_COVERAGE:.0%}. La validación pierde potencia y se sesga "
            "hacia los eventos capturados.",
        )
    ]


def check_unmatched_backlog(session: Session) -> list[Finding]:
    """Cola de entidades sin resolver.

    Cada entrada es un evento o un precio que no llegó a la base. Que crezca
    significa que el catálogo se ha quedado corto frente a lo que manda el
    proveedor.
    """
    pending = list(
        session.scalars(select(UnmatchedEntity).where(UnmatchedEntity.resolved_to_id.is_(None)))
    )
    if len(pending) < UNMATCHED_BACKLOG_WARNING:
        return []
    worst = max(pending, key=lambda row: row.occurrences)
    return [
        Finding(
            "unmatched_backlog",
            Severity.WARNING,
            f"{len(pending)} entidades sin resolver. La más frecuente: "
            f"{worst.raw_value!r} ({worst.occurrences} veces).",
        )
    ]


def check_failed_jobs(session: Session, *, now: datetime | None = None) -> list[Finding]:
    """Jobs fallidos en las últimas 24 horas."""
    moment = now or datetime.now(UTC)
    query = select(JobRun).where(
        JobRun.status == JobStatus.FAILED,
        JobRun.started_at >= moment - timedelta(hours=24),
    )
    return [
        Finding(
            "failed_job",
            Severity.CRITICAL,
            f"{job.job_name} ({job.sport_key or 'all'}) falló: "
            f"{job.error_summary or 'sin detalle'}",
            entity_type="job_run",
            entity_id=job.id,
        )
        for job in session.scalars(query)
    ]
