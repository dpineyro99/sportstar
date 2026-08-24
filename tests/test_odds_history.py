"""El histórico de odds entra por un solo sitio, y solo si pasa la auditoría."""

from __future__ import annotations

import random
from datetime import UTC, datetime
from typing import Any

import pytest

from sportstar.core.odds import implied_to_american
from sportstar.data.providers.base import RawFetch
from sportstar.data.providers.sbr_archive import PROVIDER_KEY
from sportstar.odds_history import HistoryRejected, load, run

VIG_PER_SIDE = 0.013


def _payload(n_games: int = 1200, *, seed: int = 3, broken: bool = False) -> list[dict[str, Any]]:
    """Un volcado sintético en la forma del upstream, ya bien emparejado.

    Se genera bien emparejado a propósito: aquí lo que se prueba es el encadenado
    descarga → reparación → auditoría, no el detector, que tiene sus propios tests.
    """
    rng = random.Random(seed)
    rows: list[dict[str, Any]] = []
    for i in range(n_games):
        fair = min(0.78, max(0.22, rng.gauss(0.535, 0.075)))
        noisy = min(0.78, max(0.22, 0.5 + (fair - 0.5) * 0.85 + rng.gauss(0, 0.03)))
        home_won = rng.random() < fair
        # Un histórico roto: el local pierde más de lo que gana, imposible en MLB.
        if broken:
            home_won = not home_won
        rows.append(
            {
                "season": 2011 + i // 400,
                "date": float(f"{2011 + i // 400}0402"),
                "home_team": f"H{i % 30}",
                "away_team": f"A{(i + 7) % 30}",
                "home_final": 5 if home_won else 2,
                "away_final": 2 if home_won else 5,
                "home_open_ml": implied_to_american(noisy + VIG_PER_SIDE),
                "away_open_ml": implied_to_american(1 - noisy + VIG_PER_SIDE),
                "home_close_ml": implied_to_american(fair + VIG_PER_SIDE),
                "away_close_ml": implied_to_american(1 - fair + VIG_PER_SIDE),
            }
        )
    return rows


class FakeProvider:
    provider_key = PROVIDER_KEY

    def __init__(self, payload: list[dict[str, Any]]) -> None:
        self.payload = payload
        self.calls: list[str] = []

    def fetch(self, sport: str) -> RawFetch:
        self.calls.append(sport)
        now = datetime.now(UTC)
        return RawFetch(
            provider=PROVIDER_KEY,
            endpoint="data/fake.json",
            sport_key=sport,
            payload=self.payload,
            requested_at=now,
            observed_at=now,
            http_status=200,
        )


def test_un_historico_sano_se_carga() -> None:
    provider = FakeProvider(_payload())

    history = load("mlb", provider=provider)  # type: ignore[arg-type]

    assert provider.calls == ["mlb"]
    assert len(history.games) == 1200
    assert history.audit.report.findings == []
    assert history.audit.closing_beats_opening is True


def test_un_historico_que_no_pasa_la_auditoria_no_se_usa() -> None:
    """No entra con una advertencia en el log. No entra."""
    provider = FakeProvider(_payload(broken=True))

    with pytest.raises(HistoryRejected, match="no supera la auditoría"):
        load("mlb", provider=provider)  # type: ignore[arg-type]


def test_el_mensaje_de_rechazo_dice_qué_check_falló() -> None:
    provider = FakeProvider(_payload(broken=True))

    with pytest.raises(HistoryRejected, match="home_win_rate"):
        load("mlb", provider=provider)  # type: ignore[arg-type]


def test_filtrar_temporadas() -> None:
    provider = FakeProvider(_payload())

    history = load("mlb", seasons=range(2011, 2013), provider=provider)  # type: ignore[arg-type]

    assert {g.season for g in history.games} == {2011, 2012}


def test_el_comando_informa_y_devuelve_cero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import sportstar.odds_history as module

    provider = FakeProvider(_payload())
    monkeypatch.setattr(
        module,
        "load",
        lambda sport="mlb": load(sport, provider=provider),  # type: ignore[arg-type]
    )

    assert run("mlb") == 0

    out = capsys.readouterr().out
    assert "Brier cierre" in out
    assert "% victoria local" in out
    assert "temporadas: 2011-2013" in out


def test_el_comando_devuelve_uno_si_la_auditoria_bloquea(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import sportstar.odds_history as module

    def rejecting(sport: str = "mlb") -> Any:
        raise HistoryRejected("el histórico de mlb no supera la auditoría y no se usa:\n  [x] y")

    monkeypatch.setattr(module, "load", rejecting)

    assert run("mlb") == 1
    assert "no supera la auditoría" in capsys.readouterr().out
