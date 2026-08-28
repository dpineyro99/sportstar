"""Normalización de lanzadores. La trampa de las entradas es el test que importa."""

from __future__ import annotations

from datetime import date

import pytest

from sportstar.data.normalizers.errors import ShapeError
from sportstar.data.normalizers.mlb_pitchers import (
    normalize_game_log,
    normalize_probable_starters,
    parse_innings_pitched,
)


class TestEntradasLanzadas:
    """`6.1` no es 6,1 entradas: son 6 entradas y 1 out, o sea 19 outs.

    Es la trampa clásica de los datos de béisbol. La notación **parece** decimal
    y no lo es, así que tratarla como un float mete un error silencioso en cada
    ratio por entrada — y el error es pequeño, que es lo que lo hace difícil de
    ver.
    """

    def test_entradas_completas(self) -> None:
        assert parse_innings_pitched("6.0") == 18
        assert parse_innings_pitched("7") == 21
        assert parse_innings_pitched("0.0") == 0

    def test_un_tercio_y_dos_tercios(self) -> None:
        assert parse_innings_pitched("6.1") == 19
        assert parse_innings_pitched("6.2") == 20

    def test_no_es_una_division_decimal(self) -> None:
        # Si alguien lo tratase como float, 6.1 entradas serían 18,3 outs.
        assert parse_innings_pitched("6.1") != int(6.1 * 3)

    def test_una_fraccion_que_no_existe_es_un_error(self) -> None:
        # .3 sería una entrada completa; que aparezca significa otra cosa.
        with pytest.raises(ShapeError, match=r"solo \.0, \.1 y \.2"):
            parse_innings_pitched("6.3")

    def test_texto_no_numerico(self) -> None:
        with pytest.raises(ShapeError, match="no numéricas"):
            parse_innings_pitched("seis")

    def test_texto_vacio(self) -> None:
        with pytest.raises(ShapeError, match="vacías"):
            parse_innings_pitched("   ")


def _schedule(
    *,
    game_type: str = "R",
    home_pitcher: dict[str, object] | None = None,
    away_pitcher: dict[str, object] | None = None,
    home_score: object = 5,
    away_score: object = 2,
) -> dict[str, object]:
    return {
        "dates": [
            {
                "games": [
                    {
                        "gamePk": 123,
                        "gameNumber": 1,
                        "gameType": game_type,
                        "officialDate": "2015-06-15",
                        "teams": {
                            "home": {
                                "team": {"id": 147},
                                "probablePitcher": home_pitcher,
                                "score": home_score,
                            },
                            "away": {
                                "team": {"id": 111},
                                "probablePitcher": away_pitcher,
                                "score": away_score,
                            },
                        },
                    }
                ]
            }
        ]
    }


def test_extrae_los_abridores_previstos() -> None:
    starters = normalize_probable_starters(
        _schedule(home_pitcher={"id": 477132}, away_pitcher={"id": 421685})
    )

    assert len(starters) == 1
    entry = starters[0]
    assert entry.official_date == date(2015, 6, 15)
    assert (entry.home_pitcher_id, entry.away_pitcher_id) == (477132, 421685)
    assert (entry.home_team_id, entry.away_team_id) == (147, 111)
    assert entry.complete


def test_un_partido_sin_abridor_no_esta_completo() -> None:
    starters = normalize_probable_starters(_schedule(home_pitcher={"id": 1}, away_pitcher=None))

    assert not starters[0].complete
    assert starters[0].away_pitcher_id is None


def test_el_marcador_viaja_para_desambiguar_dobles_jornadas() -> None:
    entry = normalize_probable_starters(_schedule())[0]

    assert (entry.home_score, entry.away_score) == (5, 2)


def test_un_partido_sin_jugar_no_trae_marcador() -> None:
    entry = normalize_probable_starters(_schedule(home_score=None, away_score=None))[0]

    assert entry.home_score is None


def test_la_pretemporada_se_descarta() -> None:
    """Se lanza con prospectos: el resultado no dice nada de nadie."""
    assert normalize_probable_starters(_schedule(game_type="S")) == []
    assert normalize_probable_starters(_schedule(game_type="E")) == []


def test_los_playoffs_cuentan() -> None:
    assert len(normalize_probable_starters(_schedule(game_type="W"))) == 1


def _game_log(**stat: object) -> dict[str, object]:
    base: dict[str, object] = {
        "gamesStarted": 1,
        "inningsPitched": "6.1",
        "earnedRuns": 3,
        "strikeOuts": 9,
        "baseOnBalls": 2,
        "hits": 6,
        "homeRuns": 1,
        "battersFaced": 26,
    }
    base.update(stat)
    return {"stats": [{"splits": [{"date": "2015-04-06", "gameType": "R", "stat": base}]}]}


def test_extrae_una_aparicion() -> None:
    appearances = normalize_game_log(_game_log(), pitcher_id=477132)

    assert len(appearances) == 1
    a = appearances[0]
    assert a.pitcher_id == 477132
    assert a.game_date == date(2015, 4, 6)
    assert a.is_start
    assert a.outs == 19
    assert a.innings == pytest.approx(19 / 3)
    assert (a.strikeouts, a.walks, a.home_runs) == (9, 2, 1)


def test_una_aparicion_de_relevo_no_es_apertura() -> None:
    appearances = normalize_game_log(_game_log(gamesStarted=0), pitcher_id=1)

    assert not appearances[0].is_start


def test_un_lanzador_sin_apariciones_devuelve_lista_vacia() -> None:
    """Pasa con quien cambió de liga o se lesionó en marzo. No es un error."""
    assert normalize_game_log({"stats": []}, pitcher_id=1) == []
    assert normalize_game_log({"stats": [{"splits": []}]}, pitcher_id=1) == []


def test_una_estadistica_ausente_cuenta_como_cero_y_no_revienta() -> None:
    log = _game_log()
    del log["stats"][0]["splits"][0]["stat"]["homeRuns"]  # type: ignore[index]

    assert normalize_game_log(log, pitcher_id=1)[0].home_runs == 0


def test_un_payload_con_otra_forma_dice_donde_falla() -> None:
    with pytest.raises(ShapeError, match=r"payload\.stats"):
        normalize_game_log({"stats": {}}, pitcher_id=1)
    with pytest.raises(ShapeError, match=r"payload\.dates"):
        normalize_probable_starters({"dates": {}})
