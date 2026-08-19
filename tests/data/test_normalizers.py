"""Normalizadores.

Los fixtures son la especificación ejecutable del formato esperado. Cuando se
sustituyan por capturas reales (`python -m sportstar.cli capture`), estos mismos
tests se convierten en la verificación del esquema.

La otra mitad de los tests comprueba que un payload malformado produce un
**diagnóstico útil**. Es lo que hace barata la primera ejecución real: en vez de
un `matched: 0` sin explicación, un mensaje que dice qué clave faltaba y qué
llegó en su lugar.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from sportstar.data.normalizers import ShapeError, normalize_odds, normalize_schedule
from sportstar.data.normalizers.odds_api import parse_iso8601

FIXTURES = Path(__file__).parent / "fixtures"
KNOWN_BOOKS = {"pinnacle", "draftkings", "fanduel", "circa", "betmgm"}


def load(name: str) -> object:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


class TestOddsApi:
    def test_extracts_every_event(self) -> None:
        result = normalize_odds(load("the_odds_api_odds"), sport_key="mlb")
        assert len(result.events) == 2
        assert result.errors == []

    def test_event_fields_are_normalized(self) -> None:
        result = normalize_odds(load("the_odds_api_odds"), sport_key="mlb")
        event = result.events[0]
        assert event.provider_event_id == "e912304de2b2ce35b473ce2ecd3d1502"
        assert event.home_team_raw == "New York Yankees"
        assert event.away_team_raw == "Boston Red Sox"
        assert event.start_time == datetime(2026, 8, 19, 23, 5, tzinfo=UTC)
        assert event.sport_key == "mlb"  # clave interna, no la del proveedor

    def test_moneyline_prices_are_extracted(self) -> None:
        result = normalize_odds(load("the_odds_api_odds"), sport_key="mlb")
        moneyline = [p for p in result.prices if p.market_type == "moneyline"]
        pinnacle = [p for p in moneyline if p.book_key == "pinnacle"]
        assert len(pinnacle) == 2
        assert {p.price_american for p in pinnacle} == {-120.0, 108.0}
        assert all(p.line is None for p in pinnacle)

    def test_totals_carry_the_line(self) -> None:
        result = normalize_odds(load("the_odds_api_odds"), sport_key="mlb")
        totals = [p for p in result.prices if p.market_type == "total"]
        assert {p.side_raw for p in totals} == {"Over", "Under"}
        assert all(p.line == 8.5 for p in totals)

    def test_market_keys_map_to_the_internal_taxonomy(self) -> None:
        result = normalize_odds(load("the_odds_api_odds"), sport_key="mlb")
        assert {p.market_type for p in result.prices} == {"moneyline", "total"}

    def test_unknown_books_are_recorded_not_silently_dropped(self) -> None:
        """Un book nuevo en el feed es información, no ruido.

        Puede ser un sharp que deberíamos estar usando como referencia, y
        enterarse tres meses después es tarde.
        """
        result = normalize_odds(
            load("the_odds_api_odds"), sport_key="mlb", allowed_book_keys=KNOWN_BOOKS
        )
        assert result.skipped_books == {"unknown_book_xyz"}
        assert all(p.book_key in KNOWN_BOOKS for p in result.prices)

    def test_an_event_without_prices_is_not_an_error(self) -> None:
        # Normal antes de que los books publiquen. El problema sería que NINGÚN
        # evento trajera precios, y eso lo detecta la regla matched==0.
        result = normalize_odds(load("the_odds_api_odds"), sport_key="mlb")
        dodgers = next(e for e in result.events if e.home_team_raw == "Los Angeles Dodgers")
        assert not [p for p in result.prices if p.provider_event_id == dodgers.provider_event_id]
        assert result.errors == []

    def test_does_not_resolve_teams(self) -> None:
        # Un normalizador no empareja. Lleva el texto crudo y `resolution/` decide,
        # que es quien sabe encolar lo que no resuelve.
        result = normalize_odds(load("the_odds_api_odds"), sport_key="mlb")
        assert isinstance(result.events[0].home_team_raw, str)
        assert not hasattr(result.events[0], "home_team_id")


class TestOddsApiDiagnostics:
    def test_top_level_shape_change_aborts_loudly(self) -> None:
        # Si el nivel superior cambia, nada de lo que sigue es fiable.
        with pytest.raises(ShapeError, match="se esperaba una lista"):
            normalize_odds({"events": []}, sport_key="mlb")

    def test_a_broken_event_does_not_kill_the_slate(self) -> None:
        payload = [
            {
                "id": "ok",
                "commence_time": "2026-08-19T23:05:00Z",
                "home_team": "A",
                "away_team": "B",
            },
            {"id": "roto"},
        ]
        result = normalize_odds(payload, sport_key="mlb")
        assert len(result.events) == 1
        assert len(result.errors) == 1

    def test_error_says_which_key_was_missing_and_what_arrived(self) -> None:
        """El mensaje que hace barata la primera ejecución real."""
        result = normalize_odds([{"id": "x", "home_team": "A", "away_team": "B"}], sport_key="mlb")
        message = result.errors[0]
        assert "commence_time" in message
        assert "payload[0]" in message
        assert "'id'" in message  # enumera lo que sí llegó

    def test_invalid_timestamp_is_reported_with_its_path(self) -> None:
        payload = [
            {"id": "x", "commence_time": "ayer por la tarde", "home_team": "A", "away_team": "B"}
        ]
        result = normalize_odds(payload, sport_key="mlb")
        assert "commence_time" in result.errors[0]
        assert "ISO-8601" in result.errors[0]

    def test_a_non_numeric_price_is_reported(self) -> None:
        payload = [
            {
                "id": "x",
                "commence_time": "2026-08-19T23:05:00Z",
                "home_team": "A",
                "away_team": "B",
                "bookmakers": [
                    {
                        "key": "pinnacle",
                        "markets": [
                            {
                                "key": "h2h",
                                "outcomes": [{"name": "A", "price": "menos ciento diez"}],
                            }
                        ],
                    }
                ],
            }
        ]
        result = normalize_odds(payload, sport_key="mlb")
        assert result.errors and "price" in result.errors[0]


class TestMlbSchedule:
    def test_extracts_every_game(self) -> None:
        result = normalize_schedule(load("mlb_stats_api_schedule"))
        assert len(result.events) == 3
        assert result.errors == []

    def test_game_fields_are_normalized(self) -> None:
        result = normalize_schedule(load("mlb_stats_api_schedule"))
        game = result.events[0]
        assert game.provider_event_id == "748534"
        assert game.home_team_raw == "New York Yankees"
        assert game.away_team_raw == "Boston Red Sox"
        assert game.start_time == datetime(2026, 8, 19, 23, 5, tzinfo=UTC)
        assert game.status == "scheduled"
        assert game.venue_raw == "Yankee Stadium"

    def test_status_maps_to_the_internal_enum(self) -> None:
        from sportstar.db.enums import EventStatus

        result = normalize_schedule(load("mlb_stats_api_schedule"))
        statuses = {e.status for e in result.events}
        assert statuses <= {s.value for s in EventStatus}
        assert "final" in statuses

    def test_probable_pitchers_are_captured_when_present(self) -> None:
        # El pitcher probable es la feature más importante de MLB y se conoce
        # antes del partido: hay que capturarlo desde el primer sync.
        result = normalize_schedule(load("mlb_stats_api_schedule"))
        game = result.events[0]
        assert game.home_probable_pitcher_raw == "Gerrit Cole"
        assert game.away_probable_pitcher_raw == "Brayan Bello"

    def test_missing_probable_pitcher_is_none_not_an_error(self) -> None:
        result = normalize_schedule(load("mlb_stats_api_schedule"))
        finished = next(e for e in result.events if e.status == "final")
        assert finished.home_probable_pitcher_raw is None

    def test_final_scores_are_captured(self) -> None:
        result = normalize_schedule(load("mlb_stats_api_schedule"))
        finished = next(e for e in result.events if e.status == "final")
        assert finished.home_score == 5
        assert finished.away_score == 3

    def test_provider_team_ids_are_captured_for_exact_matching(self) -> None:
        """Con el ID del proveedor, el emparejamiento deja de depender del nombre.

        Es la diferencia entre resolver por string —frágil— y resolver por
        `external_ids` —exacto.
        """
        result = normalize_schedule(load("mlb_stats_api_schedule"))
        assert result.events[0].provider_home_team_id == "147"
        assert result.events[0].provider_away_team_id == "111"

    def test_doubleheaders_keep_their_game_number(self) -> None:
        """La causa clásica de eventos duplicados.

        Los dos partidos de un doblete comparten fecha y equipos. Sin
        `gameNumber` colapsan en uno y se pierde un partido entero.
        """
        result = normalize_schedule(load("mlb_stats_api_schedule"))
        yankees_games = [e for e in result.events if e.home_team_raw == "New York Yankees"]
        assert len(yankees_games) == 2
        assert {g.game_number for g in yankees_games} == {1, 2}
        assert len({g.provider_event_id for g in yankees_games}) == 2


class TestMlbScheduleDiagnostics:
    def test_missing_dates_key_aborts_loudly(self) -> None:
        with pytest.raises(ShapeError, match="dates"):
            normalize_schedule({"totalGames": 0})

    def test_an_empty_slate_is_valid(self) -> None:
        # Un día sin partidos no es un error.
        result = normalize_schedule({"dates": []})
        assert result.events == []
        assert result.errors == []

    def test_a_broken_game_does_not_kill_the_day(self) -> None:
        payload = {"dates": [{"date": "2026-08-19", "games": [{"gamePk": 1}]}]}
        result = normalize_schedule(payload)
        assert result.events == []
        assert len(result.errors) == 1
        assert "teams" in result.errors[0]


class TestTimestampParsing:
    def test_accepts_the_zulu_suffix(self) -> None:
        assert parse_iso8601("2026-08-19T23:05:00Z", path="x") == datetime(
            2026, 8, 19, 23, 5, tzinfo=UTC
        )

    def test_accepts_explicit_offsets(self) -> None:
        parsed = parse_iso8601("2026-08-19T19:05:00-04:00", path="x")
        assert parsed.astimezone(UTC) == datetime(2026, 8, 19, 23, 5, tzinfo=UTC)

    def test_rejects_garbage_with_its_path(self) -> None:
        with pytest.raises(ShapeError, match=r"campo\.raro"):
            parse_iso8601("no es una fecha", path="campo.raro")
