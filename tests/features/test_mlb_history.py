"""Histórico de MLB -> resultados del modelo, contra la temporada 2024 real."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from sportstar.backfill import load_backfill
from sportstar.data.normalizers import normalize_schedule
from sportstar.data.normalizers.models import NormalizedEvent
from sportstar.features.elo import fit_through
from sportstar.features.mlb import TYPICAL_GAME_DURATION, to_game_results

T0 = datetime(2024, 4, 1, 18, 0, tzinfo=UTC)


def event(**kwargs: object) -> NormalizedEvent:
    base: dict = {
        "provider": "mlb-stats-api",
        "provider_event_id": "1",
        "sport_key": "mlb",
        "start_time": T0,
        "home_team_raw": "Home",
        "away_team_raw": "Away",
        "status": "final",
        "game_type": "R",
        "home_score": 5,
        "away_score": 3,
        "provider_home_team_id": "1",
        "provider_away_team_id": "2",
    }
    base.update(kwargs)
    return NormalizedEvent(**base)  # type: ignore[arg-type]


class TestFiltering:
    def test_keeps_regular_season_and_playoffs(self) -> None:
        events = [event(game_type=t) for t in ("R", "F", "D", "L", "W")]
        assert len(to_game_results(events)) == 5

    @pytest.mark.parametrize("game_type", ["S", "E", "A"])
    def test_drops_non_competitive_games(self, game_type: str) -> None:
        """Pretemporada, exhibiciones y All-Star son partidos reales que no miden
        lo que el modelo pretende medir.

        La pretemporada se juega con prospectos; las exhibiciones fueron contra
        Diablos Rojos del México y un filial de ligas menores; el All-Star lo
        juegan equipos que no existen.
        """
        assert to_game_results([event(game_type=game_type)]) == []

    def test_drops_games_that_were_not_played(self) -> None:
        # Un aplazado no es un empate ni una derrota: es un partido que no existió.
        for status in ("postponed", "cancelled", "scheduled"):
            assert to_game_results([event(status=status)]) == []

    def test_drops_finals_without_a_score(self) -> None:
        assert to_game_results([event(home_score=None)]) == []

    def test_drops_events_without_provider_team_ids(self) -> None:
        assert to_game_results([event(provider_home_team_id=None)]) == []


class TestObservedAt:
    def test_result_is_known_after_the_game_ends_not_when_it_starts(self) -> None:
        """El `observed_at` decide qué sabíamos en cada corte.

        Usar la hora de inicio haría que el resultado estuviera disponible antes
        de existir — leakage en su forma más pura.
        """
        result = to_game_results([event()])[0]
        assert result.observed_at == T0 + TYPICAL_GAME_DURATION
        assert result.observed_at > T0

    def test_duration_errs_late_on_purpose(self) -> None:
        # Errar hacia "lo supimos después" descarta información; errar hacia
        # "antes" contamina el backtest. Solo uno de los dos errores es recuperable.
        from datetime import timedelta

        assert timedelta(hours=3) <= TYPICAL_GAME_DURATION

    def test_results_come_out_chronologically(self) -> None:
        from datetime import timedelta

        events = [event(start_time=T0 + timedelta(days=d)) for d in (5, 1, 3)]
        results = to_game_results(events)
        assert [r.observed_at for r in results] == sorted(r.observed_at for r in results)


class TestAgainstTheRealSeason:
    """Verificación de extremo a extremo sobre la temporada 2024 descargada."""

    @pytest.fixture(scope="class")
    def season(self) -> list[NormalizedEvent]:
        events = [e for p in load_backfill() for e in normalize_schedule(p).events]
        if not events:
            pytest.skip("sin histórico descargado")
        return events

    def test_the_whole_season_normalizes_without_errors(self, season: list) -> None:
        errors = [err for p in load_backfill() for err in normalize_schedule(p).errors]
        assert errors == []
        assert len(season) == 2574

    def test_filtering_leaves_exactly_thirty_teams(self, season: list) -> None:
        """La prueba de que el filtro funciona.

        Sin filtrar aparecían 37 equipos: los siete de más venían de exhibiciones
        contra rivales que no son de la liga y del All-Star.
        """
        games = to_game_results(season)
        teams = {g.home_team_id for g in games} | {g.away_team_id for g in games}
        assert len(teams) == 30

    def test_each_team_plays_a_full_season(self, season: list) -> None:
        from collections import Counter

        games = to_game_results(season)
        played: Counter[int] = Counter()
        for game in games:
            played[game.home_team_id] += 1
            played[game.away_team_id] += 1
        # 162 de temporada regular, más playoffs para los que llegaron. El mínimo
        # baja a 161 porque un cancelado que no afecta a la clasificación no se
        # repone: en 2024 le pasó a Cleveland.
        assert 160 <= min(played.values()) <= 162
        assert max(played.values()) <= 166

    def test_elo_ranks_the_season_sensibly(self, season: list) -> None:
        """El campeón arriba y el peor récord de la era moderna abajo.

        No es una métrica de calidad —Elo no pretende batir al mercado— pero un
        orden absurdo delataría un error de signo o de emparejamiento.
        """
        games = to_game_results(season)
        model = fit_through(games, datetime(2025, 1, 1, tzinfo=UTC))

        names = {
            int(e.provider_home_team_id): e.home_team_raw
            for e in season
            if e.game_type == "R" and e.provider_home_team_id
        }
        ranked = [names[t] for t, _ in sorted(model.ratings.items(), key=lambda kv: -kv[1])]

        # Dodgers: campeones de 2024. White Sox: 41-121, el peor récord moderno.
        assert "Los Angeles Dodgers" in ranked[:3]
        assert ranked[-1] == "Chicago White Sox"

    def test_elo_stays_zero_sum_across_a_whole_season(self, season: list) -> None:
        model = fit_through(to_game_results(season), datetime(2025, 1, 1, tzinfo=UTC))
        total = sum(model.ratings.values())
        assert total == pytest.approx(1500.0 * len(model.ratings), abs=1e-6)

    def test_point_in_time_holds_over_real_data(self, season: list) -> None:
        # Mitad de temporada: nadie puede tener 162 partidos todavía.
        games = to_game_results(season)
        midseason = fit_through(games, datetime(2024, 7, 1, tzinfo=UTC))
        assert max(midseason.games_seen.values()) < 120
        full = fit_through(games, datetime(2025, 1, 1, tzinfo=UTC))
        assert max(full.games_seen.values()) > 160
