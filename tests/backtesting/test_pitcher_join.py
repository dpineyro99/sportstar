"""El cruce con la MLB Stats API, y la desambiguación de dobles jornadas."""

from __future__ import annotations

from datetime import UTC, date, datetime

from sportstar.backtesting.dataset import HistoricalGame, MarketPrices
from sportstar.backtesting.pitcher_join import (
    CLUB_ALIASES,
    enrich,
    join_starters,
    team_id_map,
)
from sportstar.data.normalizers.mlb_pitchers import ProbableStarters

NICKS = {"Yankees": 147, "Red Sox": 111, "Guardians": 114, "Tigers": 116}


def _game(
    *,
    home: str = "Yankees",
    away: str = "Red Sox",
    day: date = date(2015, 6, 15),
    home_score: int = 5,
    away_score: int = 2,
    sequence: int = 1,
) -> HistoricalGame:
    prices = MarketPrices(-150.0, -155.0)
    return HistoricalGame(
        season=2015,
        game_date=datetime(day.year, day.month, day.day, tzinfo=UTC),
        home_team_id=0,
        away_team_id=1,
        home_team=home,
        away_team=away,
        home_score=home_score,
        away_score=away_score,
        home=prices,
        away=prices,
        archive_sequence=sequence,
    )


def _starters(
    *,
    home_id: int = 147,
    away_id: int = 111,
    day: date = date(2015, 6, 15),
    home_score: int | None = 5,
    away_score: int | None = 2,
    home_pitcher: int | None = 900,
    away_pitcher: int | None = 901,
    game_number: int = 1,
) -> ProbableStarters:
    return ProbableStarters(
        official_date=day,
        game_pk=1000 + game_number,
        game_number=game_number,
        home_team_id=home_id,
        away_team_id=away_id,
        home_pitcher_id=home_pitcher,
        away_pitcher_id=away_pitcher,
        home_score=home_score,
        away_score=away_score,
    )


def test_el_alias_de_cleveland_apunta_al_mismo_id() -> None:
    """El archivo llega a 2021 y dice "Indians"; el id no cambió con el renombre."""
    mapping = team_id_map(NICKS)

    assert mapping["Indians"] == mapping["Guardians"] == 114
    assert "Indians" in CLUB_ALIASES


def test_un_apodo_sin_equipo_no_entra_en_el_mapa() -> None:
    assert "Guardians" not in team_id_map({"Yankees": 147})


def test_cruza_un_partido_normal() -> None:
    result = join_starters([_game()], [_starters()], team_id_map(NICKS))

    assert result.matched == {0: (900, 901)}
    assert result.match_rate == 1.0
    assert "100.0%" in result.summary()


class TestDobleJornada:
    """Dos partidos el mismo día entre los mismos equipos. El marcador los separa."""

    def test_el_marcador_desambigua(self) -> None:
        games = [
            _game(home_score=5, away_score=2, sequence=1),
            _game(home_score=1, away_score=7, sequence=2),
        ]
        starters = [
            _starters(home_score=1, away_score=7, home_pitcher=800, away_pitcher=801),
            _starters(home_score=5, away_score=2, home_pitcher=900, away_pitcher=901),
        ]

        result = join_starters(games, starters, team_id_map(NICKS))

        # Cada partido con SU abridor, aunque el orden de las dos fuentes difiera.
        assert result.matched == {0: (900, 901), 1: (800, 801)}

    def test_dos_partidos_identicos_se_descartan(self) -> None:
        """Mismo día, mismos equipos, mismo marcador: no hay forma de saber cuál."""
        games = [_game(sequence=1), _game(sequence=2)]
        starters = [
            _starters(home_pitcher=800, away_pitcher=801, game_number=1),
            _starters(home_pitcher=900, away_pitcher=901, game_number=2),
        ]

        result = join_starters(games, starters, team_id_map(NICKS))

        assert result.matched == {}
        assert result.n_ambiguous == 2


def test_un_partido_sin_abridor_se_cuenta_aparte() -> None:
    result = join_starters([_game()], [_starters(home_pitcher=None)], team_id_map(NICKS))

    assert result.matched == {}
    assert result.n_no_starters == 1
    assert result.n_unmatched == 0


def test_un_partido_que_no_existe_en_la_otra_fuente() -> None:
    result = join_starters([_game(day=date(2015, 7, 1))], [_starters()], team_id_map(NICKS))

    assert result.n_unmatched == 1


def test_un_apodo_desconocido_no_cruza() -> None:
    result = join_starters([_game(home="Zephyrs")], [_starters()], team_id_map(NICKS))

    assert result.n_unmatched == 1


def test_un_partido_sin_jugar_no_entra_en_el_indice() -> None:
    result = join_starters([_game()], [_starters(home_score=None)], team_id_map(NICKS))

    assert result.n_unmatched == 1


def test_enrich_pone_los_abridores_donde_los_hay() -> None:
    games = [_game(), _game(day=date(2015, 7, 1))]
    result = join_starters(games, [_starters()], team_id_map(NICKS))

    enriched = enrich(games, result)

    assert enriched[0].has_starters
    assert (enriched[0].home_pitcher_id, enriched[0].away_pitcher_id) == (900, 901)
    # El que no cruzó sale sin abridor, no con uno inventado.
    assert not enriched[1].has_starters
    assert enriched[1].home_pitcher_id is None


def test_enrich_no_toca_el_resto_del_partido() -> None:
    games = [_game()]
    enriched = enrich(games, join_starters(games, [_starters()], team_id_map(NICKS)))

    assert enriched[0].home_score == games[0].home_score
    assert enriched[0].home.open_american == games[0].home.open_american
