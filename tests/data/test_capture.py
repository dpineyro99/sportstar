"""Comando de captura de fixtures."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sportstar.capture import ODDS_API_KEY_ENV, run_capture


class TestWithoutNetwork:
    def test_reports_the_failure_without_destroying_existing_fixtures(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        """Un fallo de red no puede dejarnos sin la especificación del formato.

        Los fixtures previos son lo único que documenta qué esperamos de cada API:
        sobrescribirlos con basura o borrarlos al fallar sería peor que no
        capturar.
        """
        existing = tmp_path / "mlb_stats_api_schedule.json"
        existing.write_text('{"dates": []}', encoding="utf-8")
        monkeypatch.delenv(ODDS_API_KEY_ENV, raising=False)
        monkeypatch.setattr(
            "sportstar.capture.MlbStatsApiProvider.fetch_schedule",
            lambda self, target: (_ for _ in ()).throw(
                __import__("sportstar.data.http", fromlist=["HttpError"]).HttpError("sin red")
            ),
        )

        assert run_capture(tmp_path) == 1
        assert json.loads(existing.read_text()) == {"dates": []}
        assert "ERROR" in capsys.readouterr().out

    def test_skips_odds_without_an_api_key(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        monkeypatch.delenv(ODDS_API_KEY_ENV, raising=False)
        monkeypatch.setattr(
            "sportstar.capture.MlbStatsApiProvider.fetch_schedule",
            lambda self, target: (_ for _ in ()).throw(
                __import__("sportstar.data.http", fromlist=["HttpError"]).HttpError("sin red")
            ),
        )
        run_capture(tmp_path)
        assert ODDS_API_KEY_ENV in capsys.readouterr().out


class TestSuccessfulCapture:
    def test_writes_only_the_payload_never_the_url(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """La API key viaja en la query string.

        Guardar la URL de la petición metería la key en un fichero commiteado.
        """
        from datetime import UTC, datetime

        from sportstar.data.providers.base import RawFetch

        now = datetime.now(UTC)
        monkeypatch.setattr(
            "sportstar.capture.MlbStatsApiProvider.fetch_schedule",
            lambda self, target: RawFetch(
                provider="mlb-stats-api",
                endpoint="/schedule",
                sport_key="mlb",
                payload={"dates": [{"date": "2026-08-19", "games": []}]},
                requested_at=now,
                observed_at=now,
                http_status=200,
            ),
        )
        monkeypatch.delenv(ODDS_API_KEY_ENV, raising=False)

        assert run_capture(tmp_path) == 0
        written = json.loads((tmp_path / "mlb_stats_api_schedule.json").read_text())
        assert written == {"dates": [{"date": "2026-08-19", "games": []}]}
        assert "apiKey" not in (tmp_path / "mlb_stats_api_schedule.json").read_text()
