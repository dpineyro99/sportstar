"""El experimento de lanzadores: el cruce tiene que ser bueno o no hay experimento."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from sportstar.backtesting import pitcher_run
from sportstar.backtesting.dataset import HistoricalGame
from sportstar.backtesting.pitcher_join import JoinResult
from sportstar.backtesting.pitcher_run import MIN_MATCH_RATE, club_ids, prepare
from sportstar.backtesting.splits import HoldoutLedger
from sportstar.data.normalizers.mlb_pitchers import PitchingAppearance, ProbableStarters
from sportstar.data.providers.base import RawFetch
from sportstar.pitchers import PitcherHistory

from .conftest import make_games

TEAMS = {"teams": [{"id": 147, "clubName": "Yankees"}, {"id": 111, "clubName": "Red Sox"}]}


class FakeTeamsProvider:
    provider_key = "mlb-stats-api"

    def fetch_teams(self) -> RawFetch:
        now = datetime.now(UTC)
        return RawFetch(
            provider=self.provider_key,
            endpoint="/teams",
            sport_key="mlb",
            payload=TEAMS,
            requested_at=now,
            observed_at=now,
            http_status=200,
        )


def test_los_ids_de_club_salen_de_la_api() -> None:
    assert club_ids(FakeTeamsProvider()) == {"Yankees": 147, "Red Sox": 111}  # type: ignore[arg-type]


def test_un_payload_raro_no_inventa_equipos() -> None:
    class Weird(FakeTeamsProvider):
        def fetch_teams(self) -> RawFetch:
            fetch = super().fetch_teams()
            return replace(fetch, payload={"teams": [{"id": 1}, "basura"]})

    assert club_ids(Weird()) == {}  # type: ignore[arg-type]


#: Apodos sintéticos, uno por equipo. Se usan en las dos fuentes del cruce.
CLUBS = {f"T{i}": 100 + i for i in range(30)}


def _named_games(
    n_days: int = 40, *, season: int = 2011, seed: int = 5, games_per_day: int = 8
) -> list[HistoricalGame]:
    """Partidos cuyos apodos se corresponden con sus ids, para poder cruzarlos.

    Cada temporada arranca en su propio año. Si dos "temporadas" compartiesen
    fechas, los mismos equipos con el mismo marcador el mismo día serían
    indistinguibles y el cruce los descartaría por ambiguos — con razón.
    """
    return make_games(
        n_days=n_days,
        games_per_day=games_per_day,
        season=season,
        seed=seed,
        start=date(season, 4, 1),
    )


def _starters_for(games: list[HistoricalGame]) -> list[ProbableStarters]:
    """Abridores para cada partido: uno fijo por equipo, para que tengan muestra."""
    return [
        ProbableStarters(
            official_date=g.game_date.date(),
            game_pk=i,
            game_number=1,
            home_team_id=CLUBS[g.home_team],
            away_team_id=CLUBS[g.away_team],
            home_pitcher_id=900 + g.home_team_id,
            away_pitcher_id=900 + g.away_team_id,
            home_score=g.home_score,
            away_score=g.away_score,
        )
        for i, g in enumerate(games)
    ]


def _patch(monkeypatch: pytest.MonkeyPatch, games: list[HistoricalGame], starters: Any) -> None:
    class FakeOdds:
        def __init__(self) -> None:
            self.games = games

    monkeypatch.setattr(pitcher_run, "load_odds", lambda sport: FakeOdds())
    monkeypatch.setattr(pitcher_run, "to_historical_games", lambda raw: raw)
    monkeypatch.setattr(
        pitcher_run,
        "load_pitchers",
        lambda seasons: PitcherHistory(starters=starters, appearances=[]),
    )
    monkeypatch.setattr(pitcher_run, "club_ids", lambda provider=None: CLUBS)


def test_prepare_cruza_y_enriquece(monkeypatch: pytest.MonkeyPatch) -> None:
    games = _named_games()
    _patch(monkeypatch, games, _starters_for(games))

    enriched, _, join = prepare(range(2011, 2012))

    assert join.match_rate == 1.0
    assert all(g.has_starters for g in enriched)


def test_un_cruce_pobre_aborta_el_experimento(monkeypatch: pytest.MonkeyPatch) -> None:
    """Comparar modelos sobre submuestras distintas no compara nada."""
    games = _named_games()
    # Solo un tercio de los partidos existen en la otra fuente.
    _patch(monkeypatch, games, _starters_for(games)[: len(games) // 3])

    with pytest.raises(RuntimeError, match="por debajo del"):
        prepare(range(2011, 2012))


def test_el_umbral_de_cruce_es_exigente() -> None:
    assert MIN_MATCH_RATE >= 0.90


def test_el_experimento_completo_no_toca_el_holdout_por_defecto(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    train = _named_games(n_days=200, season=2011)
    test = _named_games(n_days=120, season=2019, seed=11)
    games = [*train, *test]
    starters = _starters_for(games)
    appearances = [
        PitchingAppearance(
            game_date=g.game_date.date(),
            pitcher_id=900 + team_id,
            is_start=True,
            outs=18,
            earned_runs=3,
            strikeouts=4 + team_id % 9,
            walks=2,
            hits=6,
            home_runs=1,
            batters_faced=25,
        )
        for g in games
        for team_id in (g.home_team_id, g.away_team_id)
    ]
    _patch(monkeypatch, games, starters)
    monkeypatch.setattr(
        pitcher_run,
        "load_pitchers",
        lambda seasons: PitcherHistory(starters=starters, appearances=appearances),
    )
    monkeypatch.setattr(
        pitcher_run,
        "temporal_split",
        lambda g, train=None, test=None: pitcher_run.Split(
            train=[x for x in g if x.season == 2011],
            test=[x for x in g if x.season == 2019],
        ),
    )
    ledger = HoldoutLedger(tmp_path / "ledger.json")

    assert pitcher_run.run(ledger=ledger) == 0

    out = capsys.readouterr().out
    assert "coeficientes ajustados sobre train" in out
    assert "starter_advantage" in out
    assert "holdout NO evaluado" in out
    assert ledger.uses("mlb_pitchers_2019_2019") == 0


def test_el_join_result_resume_lo_que_se_perdio() -> None:
    result = JoinResult(
        matched={0: (1, 2)},
        n_games=10,
        n_matched=1,
        n_no_starters=3,
        n_ambiguous=2,
        n_unmatched=4,
    )

    summary = result.summary()

    assert "1/10" in summary
    assert "sin abridor 3" in summary
    assert "ambiguos 2" in summary
