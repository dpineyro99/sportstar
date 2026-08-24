"""Captura periódica del mercado."""

from __future__ import annotations

import gzip
import json
from datetime import UTC, datetime
from pathlib import Path

from sportstar.sync import _write, load_snapshots

T0 = datetime(2026, 8, 23, 23, 59, tzinfo=UTC)


class TestSnapshotNaming:
    def test_writes_under_the_day_and_keeps_the_minute(self, tmp_path: Path) -> None:
        path = _write({"a": 1}, "odds", T0, tmp_path)
        assert path.parent.name == "2026-08-23"
        assert path.name == "odds_20260823T2359Z.json.gz"

    def test_two_captures_the_same_day_are_two_files(self, tmp_path: Path) -> None:
        """La secuencia de ficheros de un día **es** el movimiento de línea.

        Sobrescribir el snapshot anterior destruiría precisamente lo que hace
        falta medir, y el precio de hace una hora no se puede reconstruir.
        """
        from datetime import timedelta

        _write({"a": 1}, "odds", T0, tmp_path)
        _write({"a": 2}, "odds", T0 + timedelta(hours=1), tmp_path)
        assert len(list(tmp_path.glob("*/odds_*.json.gz"))) == 2


class TestLoadSnapshots:
    def test_reads_back_the_timestamp(self, tmp_path: Path) -> None:
        """Regresión: con doble extensión `.json.gz`, `path.stem` deja el
        `.json` pegado porque solo quita la última."""
        _write({"a": 1}, "odds", T0, tmp_path)
        loaded = load_snapshots(tmp_path, kind="odds")
        assert len(loaded) == 1
        assert loaded[0][0] == T0
        assert loaded[0][1] == {"a": 1}

    def test_returns_snapshots_in_chronological_order(self, tmp_path: Path) -> None:
        from datetime import timedelta

        for hours in (5, 1, 3):
            _write({"h": hours}, "odds", T0 + timedelta(hours=hours), tmp_path)
        stamps = [s for s, _ in load_snapshots(tmp_path, kind="odds")]
        assert stamps == sorted(stamps)

    def test_separates_kinds(self, tmp_path: Path) -> None:
        _write({"k": "odds"}, "odds", T0, tmp_path)
        _write({"k": "schedule"}, "schedule", T0, tmp_path)
        assert len(load_snapshots(tmp_path, kind="odds")) == 1
        assert load_snapshots(tmp_path, kind="schedule")[0][1] == {"k": "schedule"}

    def test_filters_by_day(self, tmp_path: Path) -> None:
        from datetime import timedelta

        _write({"d": 1}, "odds", T0, tmp_path)
        _write({"d": 2}, "odds", T0 + timedelta(days=1), tmp_path)
        assert len(load_snapshots(tmp_path, kind="odds", day=T0.date())) == 1

    def test_missing_directory_is_empty_not_an_error(self, tmp_path: Path) -> None:
        assert load_snapshots(tmp_path / "no-existe") == []

    def test_payloads_survive_the_round_trip(self, tmp_path: Path) -> None:
        payload = [{"id": "x", "home_team": "New York Yankees", "price": -115}]
        _write(payload, "odds", T0, tmp_path)
        assert load_snapshots(tmp_path, kind="odds")[0][1] == payload


class TestCompression:
    def test_snapshots_are_gzipped(self, tmp_path: Path) -> None:
        # Una temporada de capturas horarias acaba en un repositorio git.
        path = _write({"a": 1}, "odds", T0, tmp_path)
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            assert json.load(handle) == {"a": 1}


class TestQuotaAwareness:
    def test_the_capture_reports_remaining_quota(self) -> None:
        """El coste se mide, no se estima.

        El proveedor manda `x-requests-remaining` en cada respuesta, y conocer el
        consumo real es lo que decide la frecuencia de captura. Medido: 1 crédito
        por captura con h2h y región us.
        """
        from sportstar.data.http import HttpResponse

        now = datetime.now(UTC)
        response = HttpResponse(
            url="https://x/",
            status=200,
            body="[]",
            headers={"x-requests-remaining": "497"},
            requested_at=now,
            observed_at=now,
        )
        assert response.quota_remaining == 497
