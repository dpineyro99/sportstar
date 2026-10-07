"""Partición temporal y contador de usos del test set.

La regla del ROADMAP: **el test set temporal se toca una vez.** Cada iteración
sobre él lo convierte en train, porque las decisiones que se toman mirándolo
—cambiar un umbral, probar otro peso, quedarse con el mejor de tres modelos— se
ajustan a él exactamente igual que lo haría un `fit`.

El problema es que esa regla no se puede imponer con código: nada impide llamar
otra vez a la función. Lo que sí se puede hacer es **quitarle la deniabilidad**.
El contador se persiste en disco, sube en cada evaluación, y sale impreso en
cada informe. La quinta vez que se mira el test set, el informe lo dice.

Un backtest con `test_set_uses: 5` no es inválido. Es un backtest cuyo intervalo
de confianza real es más ancho que el que declara, y quien lo lea merece saberlo.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .dataset import HistoricalGame

#: Corte por defecto para MLB. Ocho temporadas de train, tres de test.
#: El corte es **temporal**, no aleatorio: partir al azar mete partidos del
#: futuro en train y produce el backtest más optimista y más falso posible.
DEFAULT_TRAIN = range(2011, 2019)
DEFAULT_TEST = range(2019, 2022)

DEFAULT_LEDGER = Path("data/backtests/holdout_ledger.json")


@dataclass(frozen=True, slots=True)
class Split:
    train: list[HistoricalGame]
    test: list[HistoricalGame]

    @property
    def train_seasons(self) -> tuple[int, ...]:
        return tuple(sorted({g.season for g in self.train}))

    @property
    def test_seasons(self) -> tuple[int, ...]:
        return tuple(sorted({g.season for g in self.test}))


def temporal_split(
    games: list[HistoricalGame],
    *,
    train: range = DEFAULT_TRAIN,
    test: range = DEFAULT_TEST,
) -> Split:
    """Parte por temporada. Las dos mitades no pueden solaparse."""
    overlap = set(train) & set(test)
    if overlap:
        raise ValueError(
            f"train y test se solapan en las temporadas {sorted(overlap)}. "
            "Un test set que contiene datos de train no mide nada."
        )
    return Split(
        train=[g for g in games if g.season in train],
        test=[g for g in games if g.season in test],
    )


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    at: str
    label: str
    uses_before: int


class HoldoutLedger:
    """Cuenta y persiste cuántas veces se ha mirado el test set."""

    def __init__(self, path: Path = DEFAULT_LEDGER) -> None:
        self._path = path

    def _read(self) -> dict[str, list[dict[str, object]]]:
        if not self._path.exists():
            return {}
        data = json.loads(self._path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}

    def uses(self, label: str) -> int:
        return len(self._read().get(label, []))

    def record(self, label: str) -> int:
        """Anota un uso y devuelve cuántos van, incluido este."""
        data = self._read()
        entries = data.setdefault(label, [])
        entries.append({"at": datetime.now(UTC).isoformat(), "uses_before": len(entries)})
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        return len(entries)

    def warning(self, label: str) -> str | None:
        """El aviso que acompaña al informe, si ya se ha mirado más de una vez."""
        uses = self.uses(label)
        if uses <= 1:
            return None
        return (
            f"⚠️  el test set '{label}' se ha evaluado {uses} veces. La primera fue "
            "una medición; las demás son ajuste. El intervalo de confianza real de "
            "este resultado es más ancho que el que declara."
        )
