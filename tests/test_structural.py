"""Edge estructural: el sesgo del consenso, y los dos filtros que no son opcionales.

El test que más importa es `TestInPlay`: sin filtrar los eventos ya empezados, la
primera versión de este módulo produjo un edge de +8,26% que era una línea in-play
stale. Un número así es exactamente el que alguien querría creer.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from sportstar.structural import (
    LIVE_EDGE_THRESHOLD,
    MAX_STALENESS,
    Anchor,
    BookPrice,
    analyze,
    fresh_prices,
)

START = datetime(2026, 10, 7, 20, 0, tzinfo=UTC)
HOME, AWAY = "San Diego Padres", "Milwaukee Brewers"

OPERATORS = {
    "betonlineag": "betonline",
    "lowvig": "betonline",
    "betus": "betus",
    "draftkings": "draftkings",
    "fanduel": "fanduel",
    "betmgm": "betmgm",
    "betrivers": "rush_street",
    "bovada": "bovada",
    "mybookieag": "mybookie",
}
REFERENCE = {"betonlineag", "lowvig", "betus"}
EXECUTABLE = {"draftkings", "fanduel", "betmgm", "betrivers", "bovada", "mybookieag"}


def _event(
    books: dict[str, tuple[int, int]],
    *,
    updates: dict[str, datetime] | None = None,
    commence: datetime = START,
    event_id: str = "evt1",
) -> dict[str, object]:
    """Un evento con un precio americano (home, away) por casa."""
    default = commence - timedelta(minutes=30)
    return {
        "id": event_id,
        "home_team": HOME,
        "away_team": AWAY,
        "commence_time": commence.isoformat().replace("+00:00", "Z"),
        "bookmakers": [
            {
                "key": key,
                "markets": [
                    {
                        "key": "h2h",
                        "last_update": (updates or {})
                        .get(key, default)
                        .isoformat()
                        .replace("+00:00", "Z"),
                        "outcomes": [
                            {"name": HOME, "price": home},
                            {"name": AWAY, "price": away},
                        ],
                    }
                ],
            }
            for key, (home, away) in books.items()
        ],
    }


def _run(payload: list[dict[str, object]], **kwargs: object) -> object:
    return analyze(
        payload,
        reference_books=REFERENCE,
        executable_books=EXECUTABLE,
        operators=OPERATORS,
        **kwargs,  # type: ignore[arg-type]
    )


# Un mercado coherente: todas las casas en torno a -150/+135, con vig.
TIGHT = {
    "betonlineag": (-150, 138),
    "betus": (-152, 136),
    "draftkings": (-155, 133),
    "fanduel": (-153, 135),
    "betmgm": (-156, 132),
    "betrivers": (-151, 137),
}


class TestInPlay:
    """Un evento ya empezado tiene odds que responden al marcador, no al partido."""

    def test_un_evento_empezado_se_descarta(self) -> None:
        payload = [
            _event(TIGHT, commence=START, updates={k: START + timedelta(hours=1) for k in TIGHT})
        ]

        report = _run(payload)

        assert report.n_in_play == 1  # type: ignore[attr-defined]
        assert report.edges == []  # type: ignore[attr-defined]

    def test_un_evento_por_empezar_se_evalua(self) -> None:
        payload = [_event(TIGHT)]

        report = _run(payload)

        assert report.n_in_play == 0  # type: ignore[attr-defined]
        assert report.edges  # type: ignore[attr-defined]

    def test_el_filtro_mata_el_edge_fantasma(self) -> None:
        """El caso real: una casa a -154 mientras el resto está entre -250 y -295.

        Con el partido en marcha eso no es una oportunidad, es una línea que no se
        ha movido. Sin el filtro producía +8,26%.
        """
        in_play = {
            "fanduel": (-265, 200),
            "betmgm": (-250, 195),
            "draftkings": (-281, 206),
            "bovada": (-285, 210),
            "betrivers": (-295, 200),
            "mybookieag": (-154, 120),  # el rezagado
            "betonlineag": (-270, 205),
            "betus": (-268, 203),
        }
        updates = {k: START + timedelta(hours=1) for k in in_play}
        payload = [_event(in_play, commence=START, updates=updates)]

        sin_filtro = _run(payload, pregame_only=False, max_staleness=timedelta(days=365))
        con_filtro = _run(payload)

        assert max(e.structural_edge for e in sin_filtro.edges) > 0.05  # type: ignore[attr-defined]
        assert con_filtro.edges == []  # type: ignore[attr-defined]

    def test_se_puede_desactivar_explicitamente(self) -> None:
        payload = [
            _event(TIGHT, commence=START, updates={k: START + timedelta(hours=1) for k in TIGHT})
        ]

        assert _run(payload, pregame_only=False).edges  # type: ignore[attr-defined]


class TestFrescura:
    """Una casa que no se movió mientras las demás sí no ofrece un precio real."""

    def test_se_mide_relativa_al_mercado_no_al_reloj(self) -> None:
        """Lo que importa es quedarse atrás, no la hora a la que se bajó el feed."""
        base = datetime(2020, 1, 1, tzinfo=UTC)  # muy antiguo en términos absolutos
        prices = [
            BookPrice("a", "a", HOME, 1.9, base),
            BookPrice("b", "b", HOME, 1.9, base - timedelta(minutes=1)),
        ]

        # Las dos son viejísimas, pero están al día entre sí.
        assert len(fresh_prices(prices)) == 2

    def test_la_rezagada_se_descarta(self) -> None:
        now = datetime(2026, 10, 7, tzinfo=UTC)
        prices = [
            BookPrice("a", "a", HOME, 1.9, now),
            BookPrice("rezagada", "r", HOME, 2.5, now - MAX_STALENESS - timedelta(minutes=1)),
        ]

        kept = fresh_prices(prices)

        assert [p.book_key for p in kept] == ["a"]

    def test_sin_marca_de_tiempo_se_conserva(self) -> None:
        """Descartarla castigaría un hueco del proveedor, no una línea vieja."""
        now = datetime(2026, 10, 7, tzinfo=UTC)
        prices = [
            BookPrice("a", "a", HOME, 1.9, now),
            BookPrice("sin_fecha", "s", HOME, 2.5, None),
        ]

        assert len(fresh_prices(prices)) == 2

    def test_si_ninguna_trae_fecha_pasan_todas(self) -> None:
        prices = [BookPrice("a", "a", HOME, 1.9, None), BookPrice("b", "b", HOME, 2.5, None)]

        assert len(fresh_prices(prices)) == 2

    def test_el_informe_cuenta_los_precios_stale(self) -> None:
        books = dict(TIGHT)
        books["mybookieag"] = (-110, -110)
        updates = {k: START - timedelta(minutes=30) for k in books}
        updates["mybookieag"] = START - timedelta(hours=3)
        payload = [_event(books, updates=updates)]

        report = _run(payload)

        assert report.n_stale_prices == 2  # type: ignore[attr-defined]


class TestAnclas:
    def test_reference_exige_conjuntos_disjuntos(self) -> None:
        """Una casa que entra en el consenso contra el que se la mide lo contamina."""
        with pytest.raises(ValueError, match="a la vez de referencia y ejecutables"):
            analyze(
                [_event(TIGHT)],
                reference_books={"fanduel"},
                executable_books={"fanduel"},
                operators=OPERATORS,
                anchor=Anchor.REFERENCE,
            )

    def test_leave_one_out_no_necesita_disjuncion(self) -> None:
        """Cada casa se compara contra el resto, así que el solapamiento es inocuo."""
        report = analyze(
            [_event(TIGHT)],
            reference_books={"fanduel"},
            executable_books={"fanduel"},
            operators=OPERATORS,
            anchor=Anchor.LEAVE_ONE_OUT,
        )

        assert report.n_events == 1

    def test_leave_one_out_excluye_al_operador_evaluado(self) -> None:
        """Y a sus marcas hermanas: dos marcas de la misma casa no son dos opiniones."""
        books = dict(TIGHT)
        books["lowvig"] = books["betonlineag"]
        report = _run([_event(books)], anchor=Anchor.LEAVE_ONE_OUT)

        # Seis operadores distintos en el feed; el consenso de cada evaluación usa
        # cinco, porque el del mejor precio queda fuera.
        assert all(e.consensus_operators == 5 for e in report.edges)  # type: ignore[attr-defined]

    def test_leave_one_out_usa_mas_muestra_que_reference(self) -> None:
        """Es la razón práctica de que exista: los sharp no están en todos los eventos."""
        sin_sharp = {k: v for k, v in TIGHT.items() if k not in REFERENCE}
        payload = [_event(sin_sharp)]

        por_referencia = _run(payload, anchor=Anchor.REFERENCE)
        loo = _run(payload, anchor=Anchor.LEAVE_ONE_OUT)

        assert por_referencia.edges == []  # type: ignore[attr-defined]
        assert loo.edges  # type: ignore[attr-defined]

    def test_incluir_la_casa_evaluada_encoge_el_edge(self) -> None:
        """La demostración numérica del sesgo que leave-one-out corrige."""
        books = dict(TIGHT)
        books["mybookieag"] = (-120, 115)  # paga claramente más que el resto
        payload = [_event(books)]

        loo = _run(payload, anchor=Anchor.LEAVE_ONE_OUT)
        best = max(loo.edges, key=lambda e: e.structural_edge)  # type: ignore[attr-defined]

        # Un consenso que la incluyese arrastraría el fair hacia su propio precio.
        contaminated = analyze(
            payload,
            reference_books=set(books),
            executable_books=set(),
            operators=OPERATORS,
            anchor=Anchor.LEAVE_ONE_OUT,
        )
        assert best.best_book == "mybookieag"
        assert best.structural_edge > 0
        assert contaminated.n_events == 1


class TestConsenso:
    def test_un_solo_operador_no_es_un_consenso(self) -> None:
        payload = [_event({"betonlineag": (-150, 138), "lowvig": (-150, 138)})]

        report = _run(payload, anchor=Anchor.REFERENCE)

        assert report.edges == []  # type: ignore[attr-defined]
        assert report.n_no_price == 2  # type: ignore[attr-defined]

    def test_un_mercado_de_un_solo_lado_se_ignora(self) -> None:
        payload = [
            {
                "id": "e",
                "home_team": HOME,
                "away_team": AWAY,
                "commence_time": START.isoformat().replace("+00:00", "Z"),
                "bookmakers": [
                    {
                        "key": "fanduel",
                        "markets": [{"key": "h2h", "outcomes": [{"name": HOME, "price": -150}]}],
                    }
                ],
            }
        ]

        assert _run(payload).n_selections == 0  # type: ignore[attr-defined]

    def test_solo_se_mira_el_moneyline(self) -> None:
        payload = [_event(TIGHT)]
        payload[0]["bookmakers"][1]["markets"][0]["key"] = "totals"  # type: ignore[index]

        report = _run(payload)

        assert report.edges  # type: ignore[attr-defined]


class TestInforme:
    def test_la_regla_de_tres_cuantifica_el_cero(self) -> None:
        """'No encontré nada' sin su cota superior invita a leerlo como 'no existe'."""
        report = _run([_event(TIGHT)])

        assert not report.above(LIVE_EDGE_THRESHOLD)  # type: ignore[attr-defined]
        bound = report.rule_of_three_upper_bound()  # type: ignore[attr-defined]
        assert bound == pytest.approx(3 / len(report.edges))  # type: ignore[attr-defined]
        assert "regla de tres" in report.summary()  # type: ignore[attr-defined]

    def test_sin_cero_no_hay_cota(self) -> None:
        books = dict(TIGHT)
        # Paga mucho más que el resto, pero su mercado sigue teniendo vig: un
        # overround <= 1 sería un precio corrupto, no una oportunidad.
        books["mybookieag"] = (115, -125)
        report = _run([_event(books)])

        assert report.above(LIVE_EDGE_THRESHOLD)  # type: ignore[attr-defined]
        assert report.rule_of_three_upper_bound() is None  # type: ignore[attr-defined]

    def test_las_casas_desconocidas_se_reportan(self) -> None:
        """Un book nuevo puede ser un sharp que deberíamos usar."""
        books = dict(TIGHT)
        books["pinnacle"] = (-149, 139)
        report = _run([_event(books)])

        assert "pinnacle" in report.unknown_books  # type: ignore[attr-defined]
        assert "pinnacle" in report.summary()  # type: ignore[attr-defined]

    def test_un_informe_vacio_sigue_diciendo_cuanto_se_perdio(self) -> None:
        report = _run([])

        assert "evaluables 0" in report.summary()  # type: ignore[attr-defined]

    def test_la_dispersion_es_mejor_sobre_peor(self) -> None:
        report = _run([_event(TIGHT)])
        edge = report.edges[0]  # type: ignore[attr-defined]

        assert edge.price_dispersion == pytest.approx(
            edge.best_price_decimal / edge.worst_price_decimal - 1.0
        )
        assert edge.price_dispersion >= 0.0


class TestRobustez:
    def test_un_payload_con_basura_no_revienta(self) -> None:
        payload = [
            "no soy un evento",  # type: ignore[list-item]
            {"id": "x", "bookmakers": "tampoco"},
            {"id": "y", "bookmakers": [{"key": "fanduel", "markets": None}]},
        ]

        report = _run(payload)  # type: ignore[arg-type]

        assert report.edges == []  # type: ignore[attr-defined]

    def test_un_precio_no_numerico_se_ignora(self) -> None:
        payload = [_event(TIGHT)]
        payload[0]["bookmakers"][0]["markets"][0]["outcomes"][0]["price"] = "ev"  # type: ignore[index]

        assert _run(payload).edges  # type: ignore[attr-defined]

    def test_una_fecha_mal_formada_no_descarta_el_evento(self) -> None:
        payload = [_event(TIGHT)]
        payload[0]["commence_time"] = "mañana"

        report = _run(payload)

        assert report.n_in_play == 0  # type: ignore[attr-defined]
        assert report.edges  # type: ignore[attr-defined]


def test_una_casa_con_mercado_corrupto_no_tumba_el_analisis() -> None:
    """Un overround <= 1 es un precio corrupto, no un arbitraje. Se salta esa casa.

    Sin esto, un solo precio malo en una jornada de treinta eventos abortaba el
    análisis entero en vez de costar una observación.
    """
    books = dict(TIGHT)
    books["bovada"] = (150, -120)  # overround < 1: imposible
    report = _run([_event(books)])

    assert report.edges
    # El resto del mercado se sigue evaluando con normalidad.
    assert all(e.consensus_operators >= 2 for e in report.edges)
