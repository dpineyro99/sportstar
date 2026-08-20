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
    """Contra la captura real del 2026-08-20 (9 partidos)."""

    def test_extracts_every_game(self) -> None:
        result = normalize_schedule(load("mlb_stats_api_schedule"))
        assert len(result.events) == 9
        assert result.errors == []

    def test_game_fields_are_normalized(self) -> None:
        result = normalize_schedule(load("mlb_stats_api_schedule"))
        game = result.events[0]
        assert game.provider_event_id == "824474"
        assert game.away_team_raw == "St. Louis Cardinals"
        assert game.home_team_raw == "Cincinnati Reds"
        assert game.start_time == datetime(2026, 8, 20, 16, 40, tzinfo=UTC)
        assert game.status == "final"
        assert game.venue_raw == "Great American Ball Park"

    def test_status_maps_to_the_internal_enum(self) -> None:
        from sportstar.db.enums import EventStatus

        result = normalize_schedule(load("mlb_stats_api_schedule"))
        statuses = {e.status for e in result.events}
        assert statuses <= {s.value for s in EventStatus}
        assert statuses == {"final", "scheduled"}

    def test_probable_pitchers_are_captured(self) -> None:
        # El pitcher probable es la feature más importante de MLB y se conoce
        # antes del partido: hay que capturarlo desde el primer sync.
        result = normalize_schedule(load("mlb_stats_api_schedule"))
        yankees = next(e for e in result.events if e.away_team_raw == "New York Yankees")
        assert yankees.away_probable_pitcher_raw == "Gerrit Cole"
        assert yankees.home_probable_pitcher_raw == "Kyle Bradish"

    def test_final_scores_are_captured(self) -> None:
        result = normalize_schedule(load("mlb_stats_api_schedule"))
        finished = next(e for e in result.events if e.provider_event_id == "824474")
        assert finished.away_score == 10
        assert finished.home_score == 9

    def test_provider_team_ids_are_captured_for_exact_matching(self) -> None:
        """Con el ID del proveedor, el emparejamiento deja de depender del nombre.

        Es la diferencia entre resolver por string —frágil— y por `external_ids`,
        que es exacto.
        """
        result = normalize_schedule(load("mlb_stats_api_schedule"))
        yankees = next(e for e in result.events if e.away_team_raw == "New York Yankees")
        assert yankees.provider_away_team_id == "147"
        assert yankees.provider_home_team_id == "110"

    def test_doubleheaders_keep_their_game_number(self) -> None:
        """La causa clásica de eventos duplicados.

        El slate capturado no traía ninguno, así que este caso usa un payload
        propio: los dos partidos comparten fecha y equipos, y sin `gameNumber`
        colapsan en uno.
        """
        payload = {
            "dates": [
                {
                    "date": "2026-08-20",
                    "games": [
                        {
                            "gamePk": 900001,
                            "gameDate": "2026-08-20T17:10:00Z",
                            "officialDate": "2026-08-20",
                            "gameNumber": 1,
                            "doubleHeader": "Y",
                            "status": {"abstractGameState": "Preview"},
                            "teams": {
                                "away": {"team": {"id": 111, "name": "Boston Red Sox"}},
                                "home": {"team": {"id": 147, "name": "New York Yankees"}},
                            },
                        },
                        {
                            "gamePk": 900002,
                            "gameDate": "2026-08-20T23:05:00Z",
                            "officialDate": "2026-08-20",
                            "gameNumber": 2,
                            "doubleHeader": "Y",
                            "status": {"abstractGameState": "Preview"},
                            "teams": {
                                "away": {"team": {"id": 111, "name": "Boston Red Sox"}},
                                "home": {"team": {"id": 147, "name": "New York Yankees"}},
                            },
                        },
                    ],
                }
            ]
        }
        result = normalize_schedule(payload)
        assert len(result.events) == 2
        assert {g.game_number for g in result.events} == {1, 2}
        assert len({g.provider_event_id for g in result.events}) == 2


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


class TestOfficialDate:
    """La jornada a la que pertenece un partido no es la fecha UTC de su inicio.

    Hallazgo de la primera captura real: 2 de 9 partidos de un slate empezaban a
    las 00:05 y 00:10 UTC del día siguiente, con `officialDate` del día anterior.
    Derivar la fecha del timestamp habría archivado el 22% de los partidos de esa
    noche en el día equivocado, partiendo cada jornada en dos.

    Es exactamente el tipo de detalle que no aparece en la documentación y que
    solo se ve mirando datos reales.
    """

    def test_night_games_belong_to_the_previous_calendar_day(self) -> None:
        result = normalize_schedule(load("mlb_stats_api_schedule"))
        crossing = [e for e in result.events if e.official_date != e.start_time.date()]
        assert crossing, "el fixture debe conservar al menos un partido que cruza medianoche UTC"
        for event in crossing:
            assert event.official_date is not None
            assert event.official_date < event.start_time.date()

    def test_every_game_of_a_slate_shares_the_official_date(self) -> None:
        # Es la propiedad que hace utilizable la fecha: una consulta por jornada
        # devuelve la noche entera, no dos mitades.
        result = normalize_schedule(load("mlb_stats_api_schedule"))
        assert len({e.official_date for e in result.events}) == 1

    def test_falls_back_to_the_utc_date_when_absent(self) -> None:
        # Incorrecto para los nocturnos, pero mejor que no tener evento.
        payload = {
            "dates": [
                {
                    "date": "2026-08-20",
                    "games": [
                        {
                            "gamePk": 1,
                            "gameDate": "2026-08-21T00:05:00Z",
                            "status": {"abstractGameState": "Preview"},
                            "teams": {
                                "away": {"team": {"id": 1, "name": "A"}},
                                "home": {"team": {"id": 2, "name": "B"}},
                            },
                        }
                    ],
                }
            ]
        }
        event = normalize_schedule(payload).events[0]
        assert event.official_date == event.start_time.date()

    def test_a_malformed_official_date_is_reported(self) -> None:
        payload = {
            "dates": [
                {
                    "date": "2026-08-20",
                    "games": [
                        {
                            "gamePk": 1,
                            "gameDate": "2026-08-20T18:00:00Z",
                            "officialDate": "el jueves",
                            "status": {"abstractGameState": "Preview"},
                            "teams": {
                                "away": {"team": {"id": 1, "name": "A"}},
                                "home": {"team": {"id": 2, "name": "B"}},
                            },
                        }
                    ],
                }
            ]
        }
        result = normalize_schedule(payload)
        assert result.errors and "officialDate" in result.errors[0]


class TestAgainstRealCapture:
    """Verificación del esquema contra una respuesta real de MLB Stats API.

    Capturada el 2026-08-20. Mientras el normalizador estuvo escrito solo contra
    documentación, estos números eran una hipótesis; ahora son un hecho.
    """

    def test_the_whole_slate_normalizes_without_errors(self) -> None:
        result = normalize_schedule(load("mlb_stats_api_schedule"))
        assert len(result.events) == 9
        assert result.errors == []

    def test_every_team_name_resolves_against_the_seeded_catalog(self, session) -> None:
        """El emparejamiento real, no uno inventado.

        Si los nombres del catálogo no coincidieran con los del proveedor, el
        sync emparejaría cero y el sistema se quedaría sin datos en silencio.
        """
        from sportstar.db.catalog import League
        from sportstar.resolution import TeamResolver
        from sportstar.seeds import seed_catalog

        seed_catalog(session)
        session.flush()
        resolver = TeamResolver(session, session.query(League).filter_by(key="mlb").one().id)

        result = normalize_schedule(load("mlb_stats_api_schedule"))
        names = {e.home_team_raw for e in result.events} | {e.away_team_raw for e in result.events}
        unresolved = [n for n in names if not resolver.resolve(n, provider="mlb-stats-api").matched]
        assert unresolved == [], f"sin resolver: {unresolved}"

    def test_probable_pitchers_are_present_for_upcoming_games(self) -> None:
        # La feature más importante de MLB, disponible antes del partido.
        result = normalize_schedule(load("mlb_stats_api_schedule"))
        upcoming = [e for e in result.events if e.status == "scheduled"]
        assert upcoming
        assert all(e.home_probable_pitcher_raw and e.away_probable_pitcher_raw for e in upcoming)

    def test_final_games_carry_scores(self) -> None:
        result = normalize_schedule(load("mlb_stats_api_schedule"))
        finals = [e for e in result.events if e.status == "final"]
        assert finals
        assert all(e.home_score is not None and e.away_score is not None for e in finals)

    def test_provider_team_ids_are_captured(self) -> None:
        result = normalize_schedule(load("mlb_stats_api_schedule"))
        assert all(e.provider_home_team_id and e.provider_away_team_id for e in result.events)
