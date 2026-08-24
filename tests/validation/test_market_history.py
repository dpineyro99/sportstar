"""Auditoría de un histórico de mercado: qué tiene que bloquear y qué dejar pasar.

Los casos se generan sintéticamente a partir de una probabilidad "verdadera" y
una semilla fija: así el test controla exactamente qué patología introduce, y
puede comprobar que el check correspondiente —y solo ese— se dispara.
"""

from __future__ import annotations

import random

import pytest

from sportstar.core.odds import implied_to_american
from sportstar.validation.market_history import (
    MarketSample,
    audit,
    samples_from_american,
)
from sportstar.validation.sanity import Severity

HOME_EDGE = 0.535
VIG_PER_SIDE = 0.013


def _priced(fair_home: float) -> tuple[float, float]:
    """Convierte una probabilidad justa en dos precios americanos con vig."""
    return (
        implied_to_american(fair_home + VIG_PER_SIDE),
        implied_to_american(1 - fair_home + VIG_PER_SIDE),
    )


def _season(
    n: int = 2000,
    *,
    seed: int = 7,
    shrink_close: float = 1.0,
    home_advantage: float = HOME_EDGE,
) -> list[tuple[float | None, float | None, float | None, float | None, bool]]:
    """Un histórico sintético bien formado.

    `shrink_close` acerca la apertura a 0,5: con el valor por defecto la apertura
    es una versión más ruidosa de la misma probabilidad, así que el cierre predice
    mejor —que es lo que pasa en un mercado real y lo que el check exige—.
    """
    rng = random.Random(seed)
    rows = []
    for _ in range(n):
        fair = min(0.80, max(0.20, rng.gauss(home_advantage, 0.075)))
        noisy = min(0.80, max(0.20, 0.5 + (fair - 0.5) * shrink_close + rng.gauss(0, 0.03)))
        open_home, open_away = _priced(noisy)
        close_home, close_away = _priced(fair)
        rows.append((open_home, open_away, close_home, close_away, rng.random() < fair))
    return rows


def test_un_historico_sano_no_dispara_nada() -> None:
    result = audit(samples_from_american(_season()))

    assert result.report.findings == []
    assert result.closing_beats_opening is True
    assert 0.50 <= result.home_win_rate <= 0.58
    assert 0.01 <= result.median_overround <= 0.08
    assert "Brier cierre" in result.summary()


def test_bloquea_local_y_visitante_cruzados() -> None:
    """El síntoma agregado de un cruce es que el local baja del 50%."""
    rows = [(oh, oa, ch, ca, not won) for oh, oa, ch, ca, won in _season()]

    result = audit(samples_from_american(rows))

    checks = {f.check for f in result.report.blocking}
    assert "home_win_rate" in checks


def test_bloquea_mercados_imposibles() -> None:
    """Dos favoritos en un mercado de dos vías: emparejamiento roto."""
    rows = _season()
    broken = [
        (oh, oa, -300.0, -300.0, won) if i % 3 == 0 else (oh, oa, ch, ca, won)
        for i, (oh, oa, ch, ca, won) in enumerate(rows)
    ]

    result = audit(samples_from_american(broken))

    checks = {f.check for f in result.report.blocking}
    assert "implausible_overround" in checks


def test_bloquea_un_cierre_peor_que_la_apertura() -> None:
    """Si la apertura predice mejor, lo más probable es que estén intercambiadas."""
    rows = [(ch, ca, oh, oa, won) for oh, oa, ch, ca, won in _season()]

    result = audit(samples_from_american(rows))

    checks = {f.check for f in result.report.blocking}
    assert "closing_beats_opening" in checks
    assert result.closing_beats_opening is False


def test_bloquea_precios_descalibrados() -> None:
    """Precios que no corresponden a esos partidos: el cierre deja de calibrar."""
    rng = random.Random(11)
    rows = _season()
    scrambled = [(oh, oa, ch, ca, rng.random() < 0.5) for oh, oa, ch, ca, _ in rows]

    result = audit(samples_from_american(scrambled))

    checks = {f.check for f in result.report.blocking}
    assert "closing_calibration" in checks


def test_exige_muestra_suficiente() -> None:
    with pytest.raises(ValueError, match="al menos"):
        audit(samples_from_american(_season(n=50)))


def test_un_historico_sin_apertura_se_audita_igual() -> None:
    rows = [(None, None, ch, ca, won) for _, _, ch, ca, won in _season()]

    result = audit(samples_from_american(rows))

    assert result.opening is None
    assert result.closing_beats_opening is None
    assert result.n_with_open == 0
    assert result.report.findings == []
    # El check de apertura no puede disparar si no hay apertura que comparar.
    assert "closing_beats_opening" not in {f.check for f in result.report.findings}


def test_sin_cierre_no_hay_nada_que_auditar() -> None:
    rows = [(oh, oa, None, None, won) for oh, oa, _, _, won in _season()]

    with pytest.raises(ValueError, match="línea de cierre"):
        audit(samples_from_american(rows))


def test_un_mercado_sin_vig_no_se_desvigoriza() -> None:
    """Quitarle vig a un mercado que suma <=1 enmascararía el dato que lo delata."""
    samples = [
        MarketSample(
            home_open_implied=None,
            away_open_implied=None,
            home_close_implied=0.40,
            away_close_implied=0.40,  # suma 0,80: imposible
            home_won=i % 2 == 0,
        )
        for i in range(300)
    ]

    with pytest.raises(ValueError, match="línea de cierre"):
        audit(samples)


def test_el_moneyline_cero_no_es_un_precio() -> None:
    rows = [(0.0, 0.0, ch, ca, won) for _, _, ch, ca, won in _season()]

    result = audit(samples_from_american(rows))

    assert result.n_with_open == 0


def test_la_severidad_de_los_hallazgos_es_bloqueante() -> None:
    rows = [(oh, oa, ch, ca, not won) for oh, oa, ch, ca, won in _season()]

    result = audit(samples_from_american(rows))

    assert result.report.blocking
    assert all(f.severity is Severity.FATAL for f in result.report.blocking)
