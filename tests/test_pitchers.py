"""Descarga y caché del histórico de lanzadores."""

from __future__ import annotations

import gzip
import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from sportstar.data.providers.base import RawFetch
from sportstar.pitchers import PitcherHistory, load, load_season

SCHEDULE: dict[str, Any] = {
    "dates": [
        {
            "games": [
                {
                    "gamePk": 1,
                    "gameNumber": 1,
                    "gameType": "R",
                    "officialDate": "2015-06-15",
                    "teams": {
                        "home": {
                            "team": {"id": 147},
                            "probablePitcher": {"id": 900},
                            "score": 5,
                        },
                        "away": {
                            "team": {"id": 111},
                            "probablePitcher": {"id": 901},
                            "score": 2,
                        },
                    },
                }
            ]
        }
    ]
}

GAME_LOG: dict[str, Any] = {
    "stats": [
        {
            "splits": [
                {
                    "date": "2015-06-15",
                    "gameType": "R",
                    "stat": {
                        "gamesStarted": 1,
                        "inningsPitched": "6.1",
                        "earnedRuns": 2,
                        "strikeOuts": 8,
                        "baseOnBalls": 1,
                        "hits": 5,
                        "homeRuns": 1,
                        "battersFaced": 24,
                    },
                }
            ]
        }
    ]
}


class FakeProvider:
    provider_key = "mlb-stats-api"

    def __init__(self) -> None:
        self.schedule_calls = 0
        self.log_calls: list[tuple[int, int]] = []

    def _fetch(self, payload: Any, endpoint: str) -> RawFetch:
        now = datetime.now(UTC)
        return RawFetch(
            provider=self.provider_key,
            endpoint=endpoint,
            sport_key="mlb",
            payload=payload,
            requested_at=now,
            observed_at=now,
            http_status=200,
        )

    def fetch_schedule_range(self, start: date, end: date) -> RawFetch:
        self.schedule_calls += 1
        return self._fetch(SCHEDULE, "/schedule")

    def fetch_pitcher_game_log(self, pitcher_id: int, season: int) -> RawFetch:
        self.log_calls.append((pitcher_id, season))
        return self._fetch(GAME_LOG, f"/people/{pitcher_id}/stats")


def test_descarga_una_temporada(tmp_path: Path) -> None:
    provider = FakeProvider()

    history = load_season(2015, cache_dir=tmp_path, provider=provider)  # type: ignore[arg-type]

    assert provider.schedule_calls == 1
    # Un game log por abridor distinto, no uno por partido.
    assert sorted(provider.log_calls) == [(900, 2015), (901, 2015)]
    assert len(history.starters) == 1
    assert history.starters[0].complete
    assert history.pitcher_ids == {900, 901}


def test_la_segunda_vez_sale_de_la_cache(tmp_path: Path) -> None:
    """~3.400 peticiones contra una API gratuita no se repiten en cada arranque."""
    first_provider = FakeProvider()
    load_season(2015, cache_dir=tmp_path, provider=first_provider)  # type: ignore[arg-type]

    second_provider = FakeProvider()
    history = load_season(2015, cache_dir=tmp_path, provider=second_provider)  # type: ignore[arg-type]

    assert second_provider.schedule_calls == 0
    assert second_provider.log_calls == []
    assert history.pitcher_ids == {900, 901}


def test_la_cache_conserva_todos_los_campos(tmp_path: Path) -> None:
    provider = FakeProvider()
    original = load_season(2015, cache_dir=tmp_path, provider=provider)  # type: ignore[arg-type]

    restored = load_season(2015, cache_dir=tmp_path, provider=FakeProvider())  # type: ignore[arg-type]

    assert restored.starters == original.starters
    assert restored.appearances == original.appearances


def test_el_marcador_sobrevive_al_viaje_por_la_cache(tmp_path: Path) -> None:
    """Sin él no se pueden desambiguar las dobles jornadas al cruzar."""
    load_season(2015, cache_dir=tmp_path, provider=FakeProvider())  # type: ignore[arg-type]

    restored = load_season(2015, cache_dir=tmp_path, provider=FakeProvider())  # type: ignore[arg-type]

    assert (restored.starters[0].home_score, restored.starters[0].away_score) == (5, 2)


def test_las_entradas_sobreviven_como_outs(tmp_path: Path) -> None:
    history = load_season(2015, cache_dir=tmp_path, provider=FakeProvider())  # type: ignore[arg-type]

    assert history.appearances[0].outs == 19


def test_varias_temporadas_se_concatenan(tmp_path: Path) -> None:
    provider = FakeProvider()

    history = load(range(2015, 2018), cache_dir=tmp_path, provider=provider)  # type: ignore[arg-type]

    assert provider.schedule_calls == 3
    assert len(history.starters) == 3
    assert len(history.appearances) == 6


def test_una_cache_corrupta_falla_en_vez_de_devolver_medio_historico(
    tmp_path: Path,
) -> None:
    """Media caché es peor que ninguna: produciría un histórico incompleto."""
    (tmp_path / "starters_2015.json.gz").write_bytes(b"no soy gzip")

    with pytest.raises(gzip.BadGzipFile):
        load_season(2015, cache_dir=tmp_path, provider=FakeProvider())  # type: ignore[arg-type]


def test_un_campo_nuevo_rompe_la_cache_vieja_en_vez_de_callar(tmp_path: Path) -> None:
    """Prefiere un KeyError a un dato silenciosamente a cero."""
    from sportstar.pitchers import _starters_from_json

    with pytest.raises(KeyError):
        _starters_from_json(json.loads(gzip.decompress(gzip.compress(b'{"d": "2015-06-15"}'))))


def test_el_historial_expone_los_lanzadores() -> None:
    assert PitcherHistory(starters=[], appearances=[]).pitcher_ids == set()


def test_las_dos_mitades_se_cachean_por_separado(tmp_path: Path) -> None:
    """Una petición contra trescientas: acoplarlas hace cara una migración barata."""
    load_season(2015, cache_dir=tmp_path, provider=FakeProvider())  # type: ignore[arg-type]
    (tmp_path / "starters_2015.json.gz").unlink()

    provider = FakeProvider()
    history = load_season(2015, cache_dir=tmp_path, provider=provider)  # type: ignore[arg-type]

    # Vuelve a pedir el calendario, pero NO los ~300 game logs.
    assert provider.schedule_calls == 1
    assert provider.log_calls == []
    assert len(history.appearances) == 2


def test_si_faltan_las_apariciones_no_se_repide_el_calendario(tmp_path: Path) -> None:
    load_season(2015, cache_dir=tmp_path, provider=FakeProvider())  # type: ignore[arg-type]
    (tmp_path / "appearances_2015.json.gz").unlink()

    provider = FakeProvider()
    load_season(2015, cache_dir=tmp_path, provider=provider)  # type: ignore[arg-type]

    assert provider.schedule_calls == 0
    assert sorted(provider.log_calls) == [(900, 2015), (901, 2015)]
