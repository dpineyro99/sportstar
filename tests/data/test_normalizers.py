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
from typing import ClassVar

import pytest

from sportstar.data.normalizers import ShapeError, normalize_odds, normalize_schedule
from sportstar.data.normalizers.odds_api import parse_iso8601

FIXTURES = Path(__file__).parent / "fixtures"
KNOWN_BOOKS = {"betonlineag", "betus", "draftkings", "fanduel", "betmgm"}


def load(name: str) -> object:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


class TestOddsApi:
    """Contra la captura real de The Odds API del 2026-08-20 (15 eventos)."""

    def test_extracts_every_event(self) -> None:
        result = normalize_odds(load("the_odds_api_odds"), sport_key="mlb")
        assert len(result.events) == 15
        assert result.errors == []

    def test_extracts_every_price(self) -> None:
        result = normalize_odds(load("the_odds_api_odds"), sport_key="mlb")
        assert len(result.prices) == 224

    def test_event_fields_are_normalized(self) -> None:
        result = normalize_odds(load("the_odds_api_odds"), sport_key="mlb")
        event = result.events[0]
        assert event.provider_event_id == "4641a1b30b5bae3cfb30564156dc2003"
        assert event.home_team_raw == "Baltimore Orioles"
        assert event.away_team_raw == "New York Yankees"
        assert event.start_time == datetime(2026, 8, 20, 22, 36, tzinfo=UTC)
        assert event.sport_key == "mlb"  # clave interna, no la del proveedor

    def test_moneyline_prices_carry_no_line(self) -> None:
        result = normalize_odds(load("the_odds_api_odds"), sport_key="mlb")
        assert {p.market_type for p in result.prices} == {"moneyline"}
        assert all(p.line is None for p in result.prices)

    def test_each_event_has_two_sides_per_book(self) -> None:
        # Sin los dos lados no se puede quitar el vig, así que un feed que
        # entregara uno solo dejaría el mercado sin fair probability.
        from collections import Counter

        result = normalize_odds(load("the_odds_api_odds"), sport_key="mlb")
        per_book = Counter((p.provider_event_id, p.book_key) for p in result.prices)
        assert set(per_book.values()) == {2}

    def test_unknown_books_are_recorded_not_silently_dropped(self) -> None:
        """Un book nuevo en el feed es información, no ruido.

        Puede ser un sharp que deberíamos estar usando como referencia, y
        enterarse tres meses después es tarde.
        """
        known = {"draftkings", "fanduel"}
        result = normalize_odds(load("the_odds_api_odds"), sport_key="mlb", allowed_book_keys=known)
        assert "bovada" in result.skipped_books
        assert all(p.book_key in known for p in result.prices)

    def test_provider_timestamps_are_per_book(self) -> None:
        # Cada casa actualiza cuando quiere: la frescura hay que medirla por
        # book, no por evento.
        result = normalize_odds(load("the_odds_api_odds"), sport_key="mlb")
        updates = {p.last_update for p in result.prices if p.last_update}
        assert len(updates) > 1

    def test_does_not_resolve_teams(self) -> None:
        # Un normalizador no empareja. Lleva el texto crudo y `resolution/`
        # decide, que es quien sabe encolar lo que no resuelve.
        result = normalize_odds(load("the_odds_api_odds"), sport_key="mlb")
        assert isinstance(result.events[0].home_team_raw, str)
        assert not hasattr(result.events[0], "home_team_id")


class TestRealBookCoverage:
    """Qué casas trae de verdad `regions=us`.

    El catálogo original apuntaba a Pinnacle, Circa y `betonline`. Ninguna de las
    tres aparece en este feed —y `betonline` ni siquiera es la clave correcta—
    así que el consenso se habría quedado vacío y el pipeline habría producido
    cero candidates desde el primer día, sin lanzar un solo error.
    """

    EXPECTED: ClassVar[set[str]] = {
        "betmgm",
        "betonlineag",
        "betrivers",
        "betus",
        "bovada",
        "draftkings",
        "fanduel",
        "lowvig",
        "mybookieag",
    }

    def test_feed_contains_the_expected_books(self) -> None:
        result = normalize_odds(load("the_odds_api_odds"), sport_key="mlb")
        assert {p.book_key for p in result.prices} == self.EXPECTED

    def test_pinnacle_is_not_available_in_the_us_region(self) -> None:
        result = normalize_odds(load("the_odds_api_odds"), sport_key="mlb")
        assert "pinnacle" not in {p.book_key for p in result.prices}

    def test_every_seeded_reference_book_exists_in_the_feed(self) -> None:
        """Regresión sobre el fallo original.

        Un book de referencia sembrado que no aparece en el feed deja el
        consenso vacío en silencio.
        """
        from sportstar.seeds.catalog import SPORTSBOOKS

        result = normalize_odds(load("the_odds_api_odds"), sport_key="mlb")
        available = {p.book_key for p in result.prices}
        referencia = {k for k, _, _, is_ref, _, _ in SPORTSBOOKS if is_ref}
        assert referencia
        assert referencia <= available


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


class TestScoresOnlyWhenStarted:
    """MLB manda `score: 0` en partidos que aún no han empezado.

    Guardar ese 0 haría un partido sin jugar indistinguible de un 0-0 terminado.
    La liquidación de apuestas depende exactamente de esa distinción, así que el
    fallo no sería cosmético: liquidaría partidos que no se han jugado.
    """

    def test_scheduled_games_carry_no_score(self) -> None:
        result = normalize_schedule(load("mlb_stats_api_schedule"))
        upcoming = [e for e in result.events if e.status == "scheduled"]
        assert upcoming
        assert all(e.home_score is None and e.away_score is None for e in upcoming)

    def test_finished_games_keep_their_score(self) -> None:
        result = normalize_schedule(load("mlb_stats_api_schedule"))
        finals = [e for e in result.events if e.status == "final"]
        assert len(finals) == 6
        assert all(e.home_score is not None for e in finals)

    def test_a_real_nil_nil_final_is_preserved(self) -> None:
        # El caso que hace importante la distinción: 2-0 con un cero legítimo.
        result = normalize_schedule(load("mlb_stats_api_schedule"))
        shutout = next(e for e in result.events if e.provider_event_id == "824589")
        assert shutout.status == "final"
        assert shutout.home_score == 0
        assert shutout.away_score == 2


class TestPostponedAndCancelled:
    """MLB marca los aplazados y cancelados como `abstractGameState: "Final"`.

    El partido "terminó" en el sentido de que ya no va a jugarse, pero tratarlo
    como terminado tiene tres consecuencias, ninguna cosmética:

    1. Data Health los marcaría eternamente como partidos sin closing line.
    2. La liquidación intentaría resolver apuestas de partidos que no se jugaron.
       Un cancelado es VOID —se devuelve el dinero—, no una derrota.
    3. Entrarían al histórico del modelo como partidos reales sin marcador.

    Solo `detailedState` los distingue.
    """

    def game(self, abstract: str, detailed: str) -> dict:
        return {
            "dates": [
                {
                    "date": "2024-04-01",
                    "games": [
                        {
                            "gamePk": 1,
                            "gameDate": "2024-04-01T18:00:00Z",
                            "officialDate": "2024-04-01",
                            "gameType": "R",
                            "status": {"abstractGameState": abstract, "detailedState": detailed},
                            "teams": {
                                "away": {"score": 0, "team": {"id": 1, "name": "A"}},
                                "home": {"score": 0, "team": {"id": 2, "name": "B"}},
                            },
                        }
                    ],
                }
            ]
        }

    @pytest.mark.parametrize(
        ("detailed", "expected"),
        [
            ("Postponed", "postponed"),
            ("Cancelled", "cancelled"),
            ("Canceled", "cancelled"),
            ("Suspended", "postponed"),
            ("Postponed: Rain", "postponed"),
        ],
    )
    def test_detailed_state_overrides_the_abstract_one(self, detailed: str, expected: str) -> None:
        event = normalize_schedule(self.game("Final", detailed)).events[0]
        assert event.status == expected

    def test_a_real_final_stays_final(self) -> None:
        assert normalize_schedule(self.game("Final", "Final")).events[0].status == "final"

    def test_a_game_that_was_not_played_carries_no_score(self) -> None:
        event = normalize_schedule(self.game("Final", "Postponed")).events[0]
        assert event.home_score is None and event.away_score is None

    def test_the_real_season_has_forty_two_unplayed_games(self) -> None:
        """Verificado sobre la temporada 2024 completa: 36 aplazados, 6 cancelados."""
        from sportstar.backfill import load_backfill

        events = [e for p in load_backfill() for e in normalize_schedule(p).events]
        if not events:
            pytest.skip("sin histórico descargado")
        unplayed = [e for e in events if e.status in ("postponed", "cancelled")]
        assert len(unplayed) == 42
        assert all(e.home_score is None for e in unplayed)


class TestGameType:
    def test_game_type_is_captured(self) -> None:
        result = normalize_schedule(load("mlb_stats_api_schedule"))
        captured = [e.game_type for e in result.events if e.game_type is not None]
        assert captured
        assert set(captured) == {"R"}

    def test_the_real_season_contains_non_competitive_games(self) -> None:
        """Por eso hace falta filtrar.

        La temporada 2024 descargada trae 93 partidos de pretemporada, 7
        exhibiciones y el All-Star mezclados con los 2.469 de temporada regular.
        """
        from collections import Counter

        from sportstar.backfill import load_backfill

        events = [e for p in load_backfill() for e in normalize_schedule(p).events]
        if not events:
            pytest.skip("sin histórico descargado")
        types = Counter(e.game_type for e in events)
        assert types["S"] > 0  # pretemporada
        assert types["E"] > 0  # exhibición
        assert types["A"] > 0  # All-Star
        assert types["R"] > 2000

    def test_missing_game_type_is_none_not_an_error(self) -> None:
        payload = {
            "dates": [
                {
                    "date": "2024-04-01",
                    "games": [
                        {
                            "gamePk": 1,
                            "gameDate": "2024-04-01T18:00:00Z",
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
        assert normalize_schedule(payload).events[0].game_type is None
