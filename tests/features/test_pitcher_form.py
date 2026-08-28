"""Forma del lanzador: encogimiento, media de liga point-in-time y ausencias."""

from __future__ import annotations

from datetime import date

import pytest

from sportstar.data.normalizers.mlb_pitchers import PitchingAppearance
from sportstar.features.mlb.pitchers import PitcherForm, PitcherTotals


def _appearance(
    pitcher_id: int = 1,
    *,
    outs: int = 18,
    k: int = 6,
    bb: int = 2,
    hr: int = 1,
    bf: int = 25,
    day: date = date(2015, 4, 6),
    start: bool = True,
) -> PitchingAppearance:
    return PitchingAppearance(
        game_date=day,
        pitcher_id=pitcher_id,
        is_start=start,
        outs=outs,
        earned_runs=3,
        strikeouts=k,
        walks=bb,
        hits=6,
        home_runs=hr,
        batters_faced=bf,
    )


def test_el_fip_usa_solo_lo_que_el_lanzador_controla() -> None:
    totals = PitcherTotals()
    totals.add(_appearance(outs=27, k=10, bb=2, hr=1))

    # (13*1 + 3*2 - 2*10) / 9 = -1/9
    assert totals.fip_core == pytest.approx(-1 / 9)


def test_sin_haber_lanzado_no_hay_fip() -> None:
    assert PitcherTotals().fip_core is None


def test_ponchar_mas_baja_el_fip() -> None:
    """Menor es mejor: el signo tiene que ir en esta dirección."""
    good, bad = PitcherTotals(), PitcherTotals()
    good.add(_appearance(k=12, bb=1, hr=0))
    bad.add(_appearance(k=2, bb=6, hr=3))

    assert good.fip_core is not None and bad.fip_core is not None
    assert good.fip_core < bad.fip_core


def test_un_lanzador_con_poca_muestra_no_se_puntua() -> None:
    """Dos home runs en cinco entradas no son una medición."""
    form = PitcherForm(min_batters_faced=50)
    form.observe(_appearance(bf=20))

    assert form.rating(1) is None


def test_el_encogimiento_acerca_a_la_media_de_liga() -> None:
    form = PitcherForm(shrinkage_bf=400.0, min_batters_faced=1)
    # Muchos lanzadores mediocres definen la liga.
    for pid in range(2, 40):
        form.observe(_appearance(pid, outs=180, k=60, bb=30, hr=20, bf=700))
    # Y uno excelente con muy poca muestra.
    form.observe(_appearance(1, outs=18, k=15, bb=0, hr=0, bf=60))

    rating = form.rating(1)
    raw = form.totals[1].fip_core
    league = form.league_fip_core

    assert rating is not None and raw is not None and league is not None
    # Su valoración queda entre lo observado y la liga, más cerca de la liga.
    assert raw < rating < league
    assert abs(rating - league) < abs(rating - raw)


def test_con_mucha_muestra_el_encogimiento_casi_no_mueve() -> None:
    form = PitcherForm(shrinkage_bf=400.0, min_batters_faced=1)
    for pid in range(2, 40):
        form.observe(_appearance(pid, outs=180, k=60, bb=30, hr=20, bf=700))
    for _ in range(40):
        form.observe(_appearance(1, outs=180, k=200, bb=20, hr=5, bf=700))

    rating = form.rating(1)
    raw = form.totals[1].fip_core

    assert rating is not None and raw is not None
    assert abs(rating - raw) < 0.35


def test_la_media_de_liga_es_la_de_hasta_ahora() -> None:
    """Leer el total de la temporada incluiría los partidos que se predicen."""
    form = PitcherForm(min_batters_faced=1)
    form.observe(_appearance(1, k=12, bb=0, hr=0))
    first = form.league_fip_core

    form.observe(_appearance(2, k=0, bb=10, hr=5))
    second = form.league_fip_core

    assert first is not None and second is not None
    assert second > first


def test_sin_liga_no_hay_valoracion() -> None:
    assert PitcherForm().rating(1) is None


def test_la_ventaja_es_positiva_cuando_el_local_lanza_mejor() -> None:
    form = PitcherForm(min_batters_faced=1)
    for pid in range(10, 40):
        form.observe(_appearance(pid, outs=180, k=60, bb=30, hr=20, bf=700))
    for _ in range(20):
        form.observe(_appearance(1, outs=180, k=220, bb=15, hr=3, bf=700))  # local, bueno
        form.observe(_appearance(2, outs=180, k=40, bb=60, hr=30, bf=700))  # visitante, malo

    advantage = form.advantage(home_pitcher_id=1, away_pitcher_id=2)

    assert advantage is not None
    assert advantage > 0.0


def test_la_ventaja_es_desconocida_si_falta_un_abridor() -> None:
    """No es una ventaja pequeña: es una ventaja que no se sabe."""
    form = PitcherForm(min_batters_faced=1)
    for pid in range(1, 40):
        form.observe(_appearance(pid, bf=700))

    assert form.advantage(None, 2) is None
    assert form.advantage(1, None) is None
    # Y tampoco si uno de los dos no tiene muestra suficiente.
    assert form.advantage(1, 99999) is None


def test_las_apariciones_de_relevo_tambien_cuentan_para_la_liga() -> None:
    form = PitcherForm(min_batters_faced=1)
    form.observe(_appearance(1, start=False, bf=10))

    assert form.league_fip_core is not None
    assert form.totals[1].starts == 0
    assert form.batters_faced(1) == 10
