"""La conversión del archivo al dominio del backtest, y su convención temporal."""

from __future__ import annotations

from datetime import UTC, date, datetime

from sportstar.backtesting.dataset import (
    DECISION_AT,
    RESULT_KNOWN_AT,
    team_ids,
    to_historical_games,
)
from sportstar.data.normalizers.sbr_archive import SbrGame


def _game(
    *,
    home: str = "Yankees",
    away: str = "Red Sox",
    day: date = date(2011, 4, 1),
    home_score: int | None = 5,
    away_score: int | None = 2,
    home_open: float | None = -150.0,
    away_open: float | None = 140.0,
    home_close: float | None = -155.0,
    away_close: float | None = 145.0,
) -> SbrGame:
    return SbrGame(
        season=2011,
        game_date=day,
        home_team_raw=home,
        away_team_raw=away,
        home_score=home_score,
        away_score=away_score,
        home_open_american=home_open,
        away_open_american=away_open,
        home_close_american=home_close,
        away_close_american=away_close,
    )


def test_la_decision_va_antes_que_el_resultado() -> None:
    """La invariante que sostiene todo el backtest."""
    assert DECISION_AT < RESULT_KNOWN_AT

    game = to_historical_games([_game()])[0]

    assert game.decided_at < game.observed_at
    assert game.decided_at == datetime(2011, 4, 1, 0, 0, tzinfo=UTC)
    assert game.observed_at == datetime(2011, 4, 1, 23, 59, tzinfo=UTC)


def test_el_resultado_de_hoy_no_esta_disponible_para_decidir_hoy() -> None:
    """Se tira información real a propósito: es el lado seguro del error."""
    games = to_historical_games([_game(), _game(home="Mets", away="Braves")])

    assert all(g.observed_at > games[0].decided_at for g in games)


def test_los_ids_de_equipo_son_estables_entre_ejecuciones() -> None:
    """Un id que cambia entre corridas hace irreproducible cualquier resultado."""
    games = [_game(), _game(home="Athletics", away="Mets")]

    assert team_ids(games) == team_ids(list(reversed(games)))


def test_los_ids_no_dependen_del_orden_de_aparicion() -> None:
    ids = team_ids([_game(home="Zephyrs", away="Athletics")])

    assert ids["Athletics"] < ids["Zephyrs"]


def test_una_doble_jornada_no_colapsa_en_un_evento() -> None:
    """341 pares reales del archivo. Sin esto, el check de duplicados bloquea todo."""
    games = to_historical_games([_game(home_score=5), _game(home_score=7, away_score=1)])

    assert [g.archive_sequence for g in games] == [1, 2]


def test_un_partido_sin_marcador_se_descarta() -> None:
    assert to_historical_games([_game(home_score=None)]) == []


def test_un_empate_se_descarta() -> None:
    """En MLB un empate es un suspendido que no se reanudó, no un resultado."""
    assert to_historical_games([_game(home_score=3, away_score=3)]) == []


def test_un_partido_al_que_le_falta_un_precio_se_descarta_entero() -> None:
    """No se completa con supuestos: se queda fuera."""
    assert to_historical_games([_game(away_close=None)]) == []
    assert to_historical_games([_game(home_open=None)]) == []


def test_el_orden_es_cronologico_y_determinista() -> None:
    games = to_historical_games(
        [
            _game(day=date(2011, 4, 3)),
            _game(day=date(2011, 4, 1), home="Athletics", away="Mets"),
            _game(day=date(2011, 4, 2), home="Cubs", away="Reds"),
        ]
    )

    assert [g.game_date.date() for g in games] == [
        date(2011, 4, 1),
        date(2011, 4, 2),
        date(2011, 4, 3),
    ]


def test_los_precios_se_convierten_en_ambos_sentidos() -> None:
    game = to_historical_games([_game()])[0]

    assert game.home.open_decimal < 2.0  # -150 es favorito
    assert game.away.open_decimal > 2.0  # +140 es underdog
    assert game.home.open_implied + game.away.open_implied > 1.0  # hay vig


def test_quien_gana_depende_del_lado() -> None:
    game = to_historical_games([_game(home_score=5, away_score=2)])[0]

    assert game.won("home") is True
    assert game.won("away") is False
    assert game.home_won is True


def test_el_result_lleva_el_observed_at_del_partido() -> None:
    game = to_historical_games([_game()])[0]

    assert game.result.observed_at == game.observed_at
    assert game.result.home_team_id == game.home_team_id
