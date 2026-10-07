"""Informes de job.

El comportamiento que este módulo existe para garantizar: un job que recibe datos
y no empareja ninguno **no puede terminar en success**.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sportstar.db.enums import JobStatus
from sportstar.workers import MATCHED, RECEIVED, UNMATCHED, JobReport


def make_report(**counters: int) -> JobReport:
    report = JobReport(job_name="sync_odds", run_id="run-1", sport_key="mlb")
    for key, value in counters.items():
        report.count(key, value)
    return report


class TestStatus:
    def test_clean_run_is_success(self) -> None:
        report = make_report(**{RECEIVED: 84, MATCHED: 84, "snapshots": 264})
        assert report.status is JobStatus.SUCCESS
        assert report.failure_reason is None

    def test_zero_matches_with_data_received_is_a_failure(self) -> None:
        """El caso que este módulo existe para atrapar.

        Sin excepciones, sin errores, contadores rellenos — y aun así el job
        falló: llegaron 84 eventos y no se emparejó ninguno. Casi siempre
        significa que cambió el formato del proveedor o se rompió el entity
        resolution, y sin esta regla el pipeline seguiría corriendo con los datos
        de ayer durante semanas.
        """
        report = make_report(**{RECEIVED: 84, MATCHED: 0})
        assert report.status is JobStatus.FAILED
        assert report.failure_reason is not None
        assert "no se emparejó ninguno" in report.failure_reason

    def test_partial_matches_are_partial(self) -> None:
        report = make_report(**{RECEIVED: 84, MATCHED: 81, UNMATCHED: 3})
        assert report.status is JobStatus.PARTIAL

    def test_an_empty_slate_is_not_a_failure(self) -> None:
        # Un día sin partidos no es un error. Sin `received > 0` la regla no aplica.
        report = make_report(**{RECEIVED: 0, MATCHED: 0})
        assert report.status is JobStatus.SUCCESS

    def test_any_error_fails_the_job(self) -> None:
        report = make_report(**{RECEIVED: 84, MATCHED: 84})
        report.error("timeout contra el proveedor")
        assert report.status is JobStatus.FAILED
        assert "timeout" in (report.failure_reason or "")

    def test_status_is_derived_not_declared(self) -> None:
        # No hay forma de marcar un job como exitoso ignorando sus contadores:
        # el estado sale de los números, no de una llamada del código del job.
        assert not hasattr(JobReport, "set_status")


class TestRendering:
    def test_renders_the_documented_block(self) -> None:
        report = make_report(**{RECEIVED: 84, MATCHED: 81, UNMATCHED: 3, "snapshots": 264})
        report.finished_at = report.started_at + timedelta(seconds=4.2)
        output = report.render()

        assert "SYNC_ODDS" in output
        assert "[mlb]" in output
        for line in ("received", "matched", "unmatched", "snapshots", "duration", "errors"):
            assert line in output
        assert "84" in output and "264" in output

    def test_failure_reason_appears_in_the_output(self) -> None:
        report = make_report(**{RECEIVED: 84, MATCHED: 0})
        assert "no se emparejó ninguno" in report.render()

    def test_duration_is_none_until_finished(self) -> None:
        report = make_report()
        assert report.duration_seconds is None
        assert report.finish().duration_seconds is not None


class TestCounters:
    def test_counters_accumulate(self) -> None:
        report = JobReport(job_name="j", run_id="r")
        report.count("snapshots", 10)
        report.count("snapshots", 5)
        assert report.get("snapshots") == 15

    def test_unknown_counter_reads_zero(self) -> None:
        assert JobReport(job_name="j", run_id="r").get("nunca_visto") == 0

    def test_started_at_is_timezone_aware(self) -> None:
        # Todo el sistema trabaja en UTC; un naive aquí se propagaría a job_runs
        # y rompería cualquier comparación con captured_at.
        report = JobReport(job_name="j", run_id="r")
        assert report.started_at.tzinfo is not None
        assert report.started_at.astimezone(UTC) <= datetime.now(UTC)


class TestErrorRendering:
    def test_errors_appear_in_the_output(self) -> None:
        report = make_report(**{RECEIVED: 10, MATCHED: 10})
        report.error("proveedor devolvió 503")
        assert "proveedor devolvió 503" in report.render()

    def test_only_the_first_errors_are_rendered(self) -> None:
        # Un job con 400 errores no debe volcar 400 líneas al log: el detalle
        # completo vive en job_runs, el log es para enterarse.
        report = make_report(**{RECEIVED: 10, MATCHED: 10})
        for i in range(20):
            report.error(f"error {i}")
        rendered = report.render()
        assert rendered.count("!  error") == 5
        assert "20 error(es)" in rendered
