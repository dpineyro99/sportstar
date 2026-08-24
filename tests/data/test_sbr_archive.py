"""El volcado histórico de SBR: detección del bug de emparejamiento y reparación.

El fixture es una jornada real —2 de abril de 2011, quince partidos— con sus
marcadores y sus moneylines de apertura y cierre reales. `_publish()` le aplica
exactamente la transformación que hace el scraper del upstream, así que el test
va de la verdad conocida a lo que el upstream publica, y comprueba que el
normalizador recorre ese camino al revés.

Usar datos reales en vez de inventados no es cosmético: el detector se basa en
que un emparejamiento erróneo produce mercados **físicamente imposibles**, y eso
solo aparece con precios reales. Con números inventados el desplazamiento puede
dar mercados perfectamente plausibles, y el test pasaría sin probar nada.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest

from sportstar.data.normalizers.errors import ShapeError
from sportstar.data.normalizers.sbr_archive import (
    Pairing,
    PairingUndecidable,
    detect_pairing,
    normalize,
)
from sportstar.data.providers.sbr_archive import (
    SPORT_FILES,
    UPSTREAM_COMMIT,
    SbrArchiveProvider,
)

SEASON = 2011
GAME_DATE = 20110402

# (visitante, local, runs visitante, runs local, open visitante, open local,
#  close visitante, close local) — MLB, 2011-04-02.
TRUE_SLATE: list[tuple[str, str, int, int, int, int, int, int]] = [
    ("Braves", "Nationals", 3, 6, -154, 134, -133, 113),
    ("Pirates", "Cubs", 3, 5, 160, -180, 157, -177),
    ("Giants", "Dodgers", 10, 0, -114, -106, -112, -108),
    ("Padres", "Cardinals", 11, 3, 125, -145, 103, -123),
    ("Astros", "Phillies", 4, 9, 215, -255, 200, -240),
    ("Mets", "Marlins", 6, 4, 152, -172, 118, -138),
    ("Brewers", "Reds", 2, 4, -101, -119, -109, -111),
    ("Diamondbacks", "Rockies", 1, 3, 129, -149, 135, -155),
    ("Twins", "Blue Jays", 1, 6, -130, 110, -115, -105),
    ("White Sox", "Indians", 8, 3, -130, 110, -124, 104),
    ("Angels", "Royals", 4, 5, -145, 125, -136, 116),
    ("Mariners", "Athletics", 5, 2, 175, -195, 150, -170),
    ("Tigers", "Yankees", 6, 10, 125, -145, 164, -184),
    ("Orioles", "Rays", 3, 1, 128, -148, 162, -182),
    ("Red Sox", "Rangers", 5, 12, -101, -119, -105, -105),
]


def _sides(
    slate: list[tuple[str, str, int, int, int, int, int, int]],
) -> list[dict[str, Any]]:
    """Aplana la jornada a la forma de la tabla de SBR: dos filas por partido."""
    rows: list[dict[str, Any]] = []
    for away, home, away_runs, home_runs, away_open, home_open, away_close, home_close in slate:
        rows.append({"team": away, "final": away_runs, "open": away_open, "close": away_close})
        rows.append({"team": home, "final": home_runs, "open": home_open, "close": home_close})
    return rows


def _row(home: dict[str, Any], away: dict[str, Any], *, season: int = SEASON) -> dict[str, Any]:
    return {
        "season": season,
        "date": float(GAME_DATE),
        "home_team": home["team"],
        "away_team": away["team"],
        "home_final": home["final"],
        "away_final": away["final"],
        "home_open_ml": home["open"],
        "away_open_ml": away["open"],
        "home_close_ml": home["close"],
        "away_close_ml": away["close"],
    }


def _publish_correctly(
    slate: list[tuple[str, str, int, int, int, int, int, int]] = TRUE_SLATE,
    *,
    season: int = SEASON,
) -> list[dict[str, Any]]:
    """Lo que el upstream publicaría si su scraper no tuviese el bug."""
    sides = _sides(slate)
    return [_row(sides[i + 1], sides[i], season=season) for i in range(0, len(sides), 2)]


def _publish_shifted(
    slate: list[tuple[str, str, int, int, int, int, int, int]] = TRUE_SLATE,
    *,
    season: int = SEASON,
) -> list[dict[str, Any]]:
    """Lo que el upstream publica de verdad.

    Su bucle se salta la primera fila de más y luego empareja de forma solapada,
    así que cada fila junta el **local del partido k** con el **visitante del
    k+1**, e intercambia las etiquetas local/visitante.
    """
    sides = _sides(slate)
    return [_row(sides[i + 2], sides[i + 1], season=season) for i in range(0, len(sides) - 2, 2)]


def test_el_fixture_desplazado_reproduce_el_sintoma() -> None:
    """Sin este síntoma el resto de los tests no probarían nada."""
    published = _publish_shifted()

    def implied(american: float) -> float:
        return 100 / (american + 100) if american > 0 else -american / (-american + 100)

    impossible = sum(
        1 for r in published if implied(r["home_close_ml"]) + implied(r["away_close_ml"]) - 1 > 0.15
    )
    assert impossible >= 3, "el fixture desplazado debe producir mercados imposibles"


def test_detecta_el_desplazamiento() -> None:
    diagnosis = detect_pairing(_publish_shifted())

    assert diagnosis.chosen is Pairing.SHIFTED
    # No basta con que gane: tiene que ganar por goleada. Eso es lo que separa
    # "detectar un bug" de "elegir a cara o cruz".
    assert (
        diagnosis.incoherence[Pairing.AS_PUBLISHED] > diagnosis.incoherence[Pairing.SHIFTED] + 0.3
    )
    assert "shifted" in diagnosis.explain()


def test_la_reparacion_reconstruye_la_jornada_real() -> None:
    games, diagnosis = normalize(_publish_shifted())

    assert diagnosis is not None and diagnosis.chosen is Pairing.SHIFTED
    # Se pierden los partidos de los extremos: del primero falta la fila de
    # visitante —la que el `next()` de más del scraper original se comió— y del
    # último falta la de local, porque el bucle solapado se detiene antes. En un
    # fichero de temporada completa eso son dos partidos de 2.460.
    recovered = [
        (
            g.away_team_raw,
            g.home_team_raw,
            g.away_score,
            g.home_score,
            g.away_open_american,
            g.home_open_american,
            g.away_close_american,
            g.home_close_american,
        )
        for g in games
    ]
    assert recovered == [
        (a, h, ar, hr, float(ao), float(ho), float(ac), float(hc))
        for a, h, ar, hr, ao, ho, ac, hc in TRUE_SLATE[1:-1]
    ]
    assert all(g.game_date == date(2011, 4, 2) for g in games)
    assert all(g.season == SEASON for g in games)


def test_un_volcado_ya_corregido_se_deja_en_paz() -> None:
    """Si el upstream arregla su bug, aplicar la corrección lo rompería."""
    diagnosis = detect_pairing(_publish_correctly())

    assert diagnosis.chosen is Pairing.AS_PUBLISHED

    games, _ = normalize(_publish_correctly())
    assert len(games) == len(TRUE_SLATE)
    assert (games[0].away_team_raw, games[0].home_team_raw) == ("Braves", "Nationals")


def test_los_codigos_de_tres_letras_se_traducen() -> None:
    """ "CUB", "KAN", "SFO"... salían sin traducir del scraper original."""
    slate = [*TRUE_SLATE, ("CUB", "SDG", 3, 5, 160, -180, 157, -177), *TRUE_SLATE]

    games, _ = normalize(_publish_shifted(slate))
    names = {g.home_team_raw for g in games} | {g.away_team_raw for g in games}

    assert "Cubs" in names and "Padres" in names
    assert not names & {"CUB", "SDG", "SFO", "SFG", "TAM", "KAN", "LOS", "BRS"}


def test_no_cruza_la_frontera_entre_temporadas() -> None:
    """Las tablas se concatenan por temporada: en la costura no hay nada que deshacer."""
    payload = [*_publish_shifted(season=2011), *_publish_shifted(season=2012)]

    games, _ = normalize(payload)

    by_season = {2011: 0, 2012: 0}
    for game in games:
        by_season[game.season] += 1
    # Cada bloque pierde su primer partido por separado; ninguna fila de 2012
    # puede emparejarse con la última de 2011.
    assert by_season == {2011: len(TRUE_SLATE) - 2, 2012: len(TRUE_SLATE) - 2}


def test_aborta_cuando_ninguna_hipotesis_es_coherente() -> None:
    """Datos rotos de otra forma no se "arreglan" eligiendo el menos malo."""
    payload = [
        {
            "season": SEASON,
            "date": float(GAME_DATE),
            "home_team": "Phillies",
            "away_team": "Astros",
            "home_final": 3,
            "away_final": 3,  # empates por todas partes
            "home_open_ml": -500,
            "away_open_ml": -500,
            "home_close_ml": -500,
            "away_close_ml": -500,
        }
        for _ in range(6)
    ]

    with pytest.raises(PairingUndecidable, match="empates"):
        detect_pairing(payload)


def test_nl_no_es_cero() -> None:
    """El volcado escribe "NL" (no line) donde no hubo precio. Un ML de 0 no existe."""
    payload = _publish_shifted()
    payload[-1]["away_open_ml"] = "NL"
    payload[-1]["away_close_ml"] = 0

    games, _ = normalize(payload)

    assert games[-1].home_open_american is None
    assert games[-1].home_close_american is None
    assert games[-1].away_close_american is not None


def test_payload_que_no_es_lista() -> None:
    with pytest.raises(ShapeError, match="payload"):
        normalize({"data": []})


def test_fila_que_no_es_objeto() -> None:
    with pytest.raises(ShapeError, match=r"payload\[1\]"):
        normalize([_publish_shifted()[0], "no soy un objeto"])


def test_fecha_invalida_dice_cual() -> None:
    payload = _publish_shifted()
    payload[0]["date"] = 20111345.0

    with pytest.raises(ShapeError, match="20111345"):
        normalize(payload, pairing=Pairing.AS_PUBLISHED)


def test_temporada_ausente() -> None:
    payload = _publish_shifted()
    payload[0]["season"] = None

    with pytest.raises(ShapeError, match="season"):
        normalize(payload, pairing=Pairing.AS_PUBLISHED)


def test_el_provider_esta_pinchado_a_un_commit() -> None:
    """Un backtest cuyo dataset puede cambiar solo no es un backtest reproducible."""
    assert len(UPSTREAM_COMMIT) == 40
    assert all(c in "0123456789abcdef" for c in UPSTREAM_COMMIT)


def test_el_provider_rechaza_deportes_desconocidos() -> None:
    provider = SbrArchiveProvider()

    with pytest.raises(ValueError, match="no está en el volcado"):
        provider.fetch("cricket")


def test_la_cache_lleva_el_commit_en_el_nombre(tmp_path: Path) -> None:
    """Pedir otro commit no puede leer la caché del anterior."""
    provider = SbrArchiveProvider(cache_dir=tmp_path)
    path = provider._cache_path("mlb")

    assert path is not None
    assert UPSTREAM_COMMIT[:12] in path.name


def test_la_cache_evita_la_segunda_descarga(tmp_path: Path) -> None:
    import json as _json
    from datetime import UTC, datetime

    from sportstar.data.http import HttpResponse

    calls: list[str] = []

    class FakeClient:
        def get(self, url: str, params: Any = None) -> HttpResponse:
            calls.append(url)
            now = datetime.now(UTC)
            return HttpResponse(
                url=url,
                status=200,
                body=_json.dumps(_publish_shifted()),
                headers={},
                requested_at=now,
                observed_at=now,
            )

    provider = SbrArchiveProvider(client=FakeClient(), cache_dir=tmp_path)  # type: ignore[arg-type]

    first = provider.fetch("mlb")
    second = provider.fetch("mlb")

    assert len(calls) == 1
    assert SPORT_FILES["mlb"] in calls[0]
    assert first.payload == second.payload
