"""Ejecución y persistencia de los checks de Data Health.

Los hallazgos se persisten en `data_health_checks` en vez de solo imprimirse,
por dos razones: para que el panel pueda mostrarlos, y para poder responder
después "¿cuánto tiempo llevaba roto esto?". Un problema que se detecta y se
olvida es casi tan malo como uno que no se detecta.

Los hallazgos que dejan de aparecer se marcan resueltos automáticamente. Sin eso,
el panel se llena de ruido histórico y deja de mirarse — que es la forma habitual
en que muere un sistema de alertas.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.enums import Severity
from ..db.ops import DataHealthCheck
from .checks import (
    Finding,
    check_closing_coverage,
    check_closing_lines_missing,
    check_events_without_odds,
    check_failed_jobs,
    check_impossible_probabilities,
    check_odds_after_start,
    check_stale_odds,
    check_unmatched_backlog,
)

CheckFn = Callable[..., list[Finding]]

# Orden de ejecución = orden de presentación en el panel. Primero lo
# irrecuperable, después lo que rompe hoy, después lo que degrada.
ALL_CHECKS: tuple[CheckFn, ...] = (
    check_closing_lines_missing,
    check_closing_coverage,
    check_stale_odds,
    check_failed_jobs,
    check_impossible_probabilities,
    check_events_without_odds,
    check_unmatched_backlog,
    check_odds_after_start,
)

_NEEDS_NOW = frozenset(
    {
        "check_closing_lines_missing",
        "check_closing_coverage",
        "check_stale_odds",
        "check_events_without_odds",
        "check_failed_jobs",
    }
)


@dataclass(frozen=True, slots=True)
class HealthReport:
    findings: list[Finding]
    checked_at: datetime

    @property
    def critical(self) -> list[Finding]:
        return [f for f in self.findings if f.severity is Severity.CRITICAL]

    @property
    def is_healthy(self) -> bool:
        """Solo los CRITICAL rompen la salud.

        Un WARNING permanente que marcase el sistema como enfermo entrenaría a
        cualquiera a ignorar el indicador.
        """
        return not self.critical

    def by_severity(self, severity: Severity) -> list[Finding]:
        return [f for f in self.findings if f.severity is severity]

    def render(self) -> str:
        if not self.findings:
            return "DATA HEALTH  ok  (sin hallazgos)"

        lines = [f"DATA HEALTH  {'ok' if self.is_healthy else 'CRITICAL'}"]
        for severity in (Severity.CRITICAL, Severity.WARNING, Severity.INFO):
            for finding in self.by_severity(severity):
                lines.append(
                    f"  [{str(severity).upper():<8}] {finding.check_name}: {finding.message}"
                )
        return "\n".join(lines)


def run_checks(
    session: Session,
    *,
    now: datetime | None = None,
    checks: Sequence[CheckFn] = ALL_CHECKS,
) -> HealthReport:
    """Ejecuta los checks sin escribir nada. Útil para el CLI y los tests."""
    moment = now or datetime.now(UTC)
    findings: list[Finding] = []
    for check in checks:
        if check.__name__ in _NEEDS_NOW:
            findings.extend(check(session, now=moment))
        else:
            findings.extend(check(session))
    return HealthReport(findings, moment)


def persist_report(session: Session, report: HealthReport) -> tuple[int, int]:
    """Sincroniza los hallazgos con `data_health_checks`.

    Devuelve `(nuevos, resueltos)`. Un hallazgo que sigue apareciendo **no** se
    duplica: se conserva la fila original, y con ella su `detected_at`. Eso es lo
    que permite responder "¿desde cuándo?", que suele ser la primera pregunta
    útil ante un problema.
    """
    open_rows = {
        (row.check_name, row.entity_id): row
        for row in session.scalars(
            select(DataHealthCheck).where(DataHealthCheck.resolved_at.is_(None))
        )
    }
    current = {(f.check_name, f.entity_id): f for f in report.findings}

    created = 0
    for key, finding in current.items():
        if key in open_rows:
            continue
        session.add(
            DataHealthCheck(
                check_name=finding.check_name,
                severity=finding.severity,
                entity_type=finding.entity_type,
                entity_id=finding.entity_id,
                message=finding.message,
                detected_at=report.checked_at,
            )
        )
        created += 1

    resolved = 0
    for key, row in open_rows.items():
        if key not in current:
            row.resolved_at = report.checked_at
            resolved += 1

    session.flush()
    return created, resolved
