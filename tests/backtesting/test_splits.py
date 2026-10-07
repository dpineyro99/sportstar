"""Partición temporal y el contador de usos del test set."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from sportstar.backtesting.splits import HoldoutLedger, temporal_split

from .conftest import make_games


def _two_seasons() -> list:  # type: ignore[type-arg]
    return [
        *make_games(n_days=10, season=2011, start=date(2011, 4, 1)),
        *make_games(n_days=10, season=2019, start=date(2019, 4, 1)),
    ]


def test_el_corte_es_por_temporada() -> None:
    split = temporal_split(_two_seasons(), train=range(2011, 2012), test=range(2019, 2020))

    assert split.train_seasons == (2011,)
    assert split.test_seasons == (2019,)


def test_train_y_test_no_pueden_solaparse() -> None:
    """Un test set que contiene datos de train no mide nada."""
    with pytest.raises(ValueError, match="se solapan"):
        temporal_split(_two_seasons(), train=range(2011, 2020), test=range(2019, 2021))


def test_el_corte_no_mezcla_partidos() -> None:
    split = temporal_split(_two_seasons(), train=range(2011, 2012), test=range(2019, 2020))

    assert all(g.season == 2011 for g in split.train)
    assert all(g.season == 2019 for g in split.test)


def test_el_ledger_cuenta_los_usos(tmp_path: Path) -> None:
    ledger = HoldoutLedger(tmp_path / "ledger.json")

    assert ledger.uses("mlb") == 0
    assert ledger.record("mlb") == 1
    assert ledger.record("mlb") == 2
    assert ledger.uses("mlb") == 2


def test_el_ledger_persiste_entre_instancias(tmp_path: Path) -> None:
    """Si se olvidara al reiniciar, el contador no serviría para nada."""
    path = tmp_path / "ledger.json"
    HoldoutLedger(path).record("mlb")

    assert HoldoutLedger(path).uses("mlb") == 1


def test_cada_conjunto_lleva_su_propia_cuenta(tmp_path: Path) -> None:
    ledger = HoldoutLedger(tmp_path / "ledger.json")
    ledger.record("mlb_2019_2021")

    assert ledger.uses("mlb_2019_2021") == 1
    assert ledger.uses("nba_2019_2021") == 0


def test_el_primer_uso_no_lleva_aviso(tmp_path: Path) -> None:
    ledger = HoldoutLedger(tmp_path / "ledger.json")
    ledger.record("mlb")

    assert ledger.warning("mlb") is None


def test_a_partir_del_segundo_uso_el_informe_lo_dice(tmp_path: Path) -> None:
    ledger = HoldoutLedger(tmp_path / "ledger.json")
    ledger.record("mlb")
    ledger.record("mlb")

    warning = ledger.warning("mlb")
    assert warning is not None
    assert "2 veces" in warning


def test_un_ledger_corrupto_no_tumba_el_backtest(tmp_path: Path) -> None:
    path = tmp_path / "ledger.json"
    path.write_text("[]", encoding="utf-8")

    assert HoldoutLedger(path).uses("mlb") == 0
