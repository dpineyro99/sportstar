"""Informes de ejecución de jobs.

Regla que gobierna este módulo: **un job que no encuentra nada es un fallo, no un
éxito silencioso.** Los procesos que fallan calladamente son la causa raíz de
casi todo backtest engañoso: el pipeline sigue corriendo, las tablas siguen
teniendo datos de ayer, y nadie se entera hasta que los números no cuadran meses
después.

Por eso `matched == 0` con `received > 0` termina en `FAILED` aunque no se haya
lanzado ninguna excepción.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from ..db.enums import JobStatus

# Contadores con significado especial para la regla de estado.
RECEIVED = "received"
MATCHED = "matched"
UNMATCHED = "unmatched"


@dataclass
class JobReport:
    """Acumula contadores y errores de una ejecución, y decide su estado.

    Mutable a propósito: se va rellenando durante el job. `finish()` lo cierra.
    """

    job_name: str
    run_id: str
    sport_key: str | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None
    counters: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def count(self, key: str, n: int = 1) -> None:
        self.counters[key] = self.counters.get(key, 0) + n

    def error(self, message: str) -> None:
        self.errors.append(message)

    def get(self, key: str) -> int:
        return self.counters.get(key, 0)

    @property
    def status(self) -> JobStatus:
        """Estado derivado de los contadores, no declarado por el llamante.

        Que lo derive el informe y no el código del job es deliberado: un job no
        puede declararse exitoso ignorando sus propios números.
        """
        if self.errors:
            return JobStatus.FAILED
        # El caso que este módulo existe para atrapar: llegaron datos y no se
        # emparejó ni uno. Casi siempre significa que cambió el formato del
        # proveedor o que se rompió el entity resolution.
        if self.get(RECEIVED) > 0 and self.get(MATCHED) == 0:
            return JobStatus.FAILED
        if self.get(UNMATCHED) > 0:
            return JobStatus.PARTIAL
        return JobStatus.SUCCESS

    @property
    def failure_reason(self) -> str | None:
        """Por qué falló, en una línea. `None` si no falló."""
        if self.errors:
            return f"{len(self.errors)} error(es): {self.errors[0]}"
        if self.get(RECEIVED) > 0 and self.get(MATCHED) == 0:
            return (
                f"se recibieron {self.get(RECEIVED)} eventos y no se emparejó ninguno. "
                "Revisar formato del proveedor y entity resolution."
            )
        return None

    def finish(self) -> JobReport:
        self.finished_at = datetime.now(UTC)
        return self

    @property
    def duration_seconds(self) -> float | None:
        if self.finished_at is None:
            return None
        return (self.finished_at - self.started_at).total_seconds()

    def render(self) -> str:
        """Bloque legible para el log.

        SYNC ODDS  [mlb]  status=partial  run_id=abc123
          received        :   84
          matched         :   81
          unmatched       :    3
          snapshots       :  264
          duration        : 4.2s
          errors          :    0
        """
        header = (
            f"{self.job_name.upper()}  [{self.sport_key or 'all'}]  "
            f"status={self.status}  run_id={self.run_id}"
        )
        lines = [header]
        for key, value in self.counters.items():
            lines.append(f"  {key:<16}: {value:>5}")
        if self.duration_seconds is not None:
            lines.append(f"  {'duration':<16}: {self.duration_seconds:>5.1f}s")
        lines.append(f"  {'errors':<16}: {len(self.errors):>5}")
        if self.failure_reason:
            lines.append(f"  -> {self.failure_reason}")
        for err in self.errors[:5]:
            lines.append(f"  !  {err}")
        return "\n".join(lines)
