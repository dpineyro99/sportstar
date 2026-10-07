"""Checks anti-bug sobre resultados aparentemente buenos.

Principio: **un resultado extraordinario es un bug hasta demostrar lo contrario.**
Antes de creer que encontramos una mina de oro hay que descartar leakage, muestra
insuficiente, odds stale, settlement incorrecto, eventos duplicados y errores de
vig — que es lo que produce el 99% de los backtests espectaculares.

Estos checks son **bloqueantes**: un backtest que dispara un `FATAL` no produce un
número con asterisco, produce un error. `Backtest.passed_sanity = False` impide
mostrar métricas.

Funciones puras, sin dependencia de la base de datos, para poder testearlas con
casos construidos a mano.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Hashable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

# --- Umbrales -----------------------------------------------------------------
# Calibrados por orden de magnitud, no por precisión: su función es disparar una
# investigación, no clasificar. Ver CURRENT_SYSTEM_AUDIT.md §2.3.

MIN_BETS_FOR_ROI_CLAIM = 500
SUSPICIOUS_ROI = 0.15
SUSPICIOUS_WIN_RATE = 0.60
PICKEM_DECIMAL_RANGE = (1.80, 2.20)
MIN_BETS_FOR_WIN_RATE_CHECK = 100
SUSPICIOUS_MEAN_EDGE = 0.03
MIN_CANDIDATES_FOR_EDGE_CHECK = 200
MIN_CLOSING_COVERAGE = 0.95


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    FATAL = "fatal"


@dataclass(frozen=True, slots=True)
class SanityFinding:
    check: str
    severity: Severity
    message: str
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SanityReport:
    findings: list[SanityFinding]

    @property
    def blocking(self) -> list[SanityFinding]:
        return [f for f in self.findings if f.severity is Severity.FATAL]

    @property
    def passed(self) -> bool:
        """False si hay algún FATAL. Las métricas no se muestran."""
        return not self.blocking

    def as_dict(self) -> list[dict[str, Any]]:
        """Serialización para `Backtest.sanity_checks`."""
        return [
            {"check": f.check, "severity": str(f.severity), "message": f.message, **f.detail}
            for f in self.findings
        ]


# --- Checks individuales ------------------------------------------------------


def check_roi_vs_sample_size(n_bets: int, roi: float) -> list[SanityFinding]:
    """ROI espectacular con muestra pequeña.

    Un +30% de ROI sobre 80 apuestas no es una ventaja, es ruido — o leakage.
    Demostrar un ROI real del +3% exige del orden de 5.000-8.000 apuestas.
    """
    if roi > SUSPICIOUS_ROI and n_bets < MIN_BETS_FOR_ROI_CLAIM:
        return [
            SanityFinding(
                "roi_vs_sample_size",
                Severity.FATAL,
                f"ROI {roi:.1%} sobre {n_bets} apuestas. Con menos de "
                f"{MIN_BETS_FOR_ROI_CLAIM} el resultado no es interpretable: "
                "investigar leakage, settlement y odds stale antes de creerlo.",
                {"n_bets": n_bets, "roi": roi},
            )
        ]
    return []


def check_win_rate(n_bets: int, win_rate: float, avg_decimal_odds: float) -> list[SanityFinding]:
    """Win rate imposible para el precio medio pagado.

    Acertar el 65% a precios de moneda al aire es matemáticamente posible y
    empíricamente sospechosísimo: suele significar que el settlement asignó
    ganadores mirando el resultado con información que la apuesta no tenía.
    """
    lo, hi = PICKEM_DECIMAL_RANGE
    if (
        n_bets >= MIN_BETS_FOR_WIN_RATE_CHECK
        and win_rate > SUSPICIOUS_WIN_RATE
        and lo <= avg_decimal_odds <= hi
    ):
        return [
            SanityFinding(
                "win_rate_vs_price",
                Severity.FATAL,
                f"Win rate {win_rate:.1%} con cuota media {avg_decimal_odds:.2f} "
                f"(≈ pick'em) sobre {n_bets} apuestas. Revisar settlement y "
                "emparejamiento de selecciones.",
                {"n_bets": n_bets, "win_rate": win_rate, "avg_decimal_odds": avg_decimal_odds},
            )
        ]
    return []


def check_feature_leakage(
    samples: Sequence[tuple[datetime, datetime]],
) -> list[SanityFinding]:
    """Features construidas con información posterior al momento de la apuesta.

    Cada tupla es `(feature_as_of, source_observed_at)`. El invariante es
    `source_observed_at < feature_as_of`. Es leakage duro y siempre FATAL: no
    existe una cantidad aceptable.
    """
    violations = [(a, o) for a, o in samples if o >= a]
    if violations:
        return [
            SanityFinding(
                "feature_leakage",
                Severity.FATAL,
                f"{len(violations)} features derivadas de datos observados en o "
                "después de su as_of. Leakage: los resultados no significan nada.",
                {"violations": len(violations), "first": str(violations[0][0])},
            )
        ]
    return []


def check_odds_after_start(
    samples: Sequence[tuple[datetime, datetime]],
) -> list[SanityFinding]:
    """Precios capturados después del inicio del evento usados como pregame.

    Cada tupla es `(captured_at, event_start_time)`. Un precio in-play refleja lo
    que ya está pasando en el campo; usarlo como si fuera pregame produce edge
    fantasma enorme y muy convincente.
    """
    violations = [(c, s) for c, s in samples if c >= s]
    if violations:
        return [
            SanityFinding(
                "odds_after_start",
                Severity.FATAL,
                f"{len(violations)} precios capturados en o después del inicio del "
                "evento usados como pregame.",
                {"violations": len(violations)},
            )
        ]
    return []


def check_market_overround(
    markets: Sequence[tuple[Hashable, Sequence[float]]],
) -> list[SanityFinding]:
    """Mercados cuyas implied suman menos de 1.

    Es arbitraje aparente. Con precios reales casi siempre significa un precio
    corrupto, o dos lados que no pertenecen al mismo mercado por un fallo de
    emparejamiento — no una oportunidad.
    """
    bad = [(key, sum(probs)) for key, probs in markets if sum(probs) < 1.0]
    if bad:
        return [
            SanityFinding(
                "market_overround",
                Severity.FATAL,
                f"{len(bad)} mercados con overround < 1 (arbitraje aparente). "
                "Revisar emparejamiento de selecciones y precios corruptos.",
                {"count": len(bad), "worst": min(o for _, o in bad)},
            )
        ]
    return []


def check_duplicate_events(keys: Sequence[Hashable]) -> list[SanityFinding]:
    """Eventos duplicados.

    Un partido contado dos veces duplica sus apuestas y sesga las métricas hacia
    lo que pasó en ese partido concreto.
    """
    dupes = {k: n for k, n in Counter(keys).items() if n > 1}
    if dupes:
        return [
            SanityFinding(
                "duplicate_events",
                Severity.FATAL,
                f"{len(dupes)} eventos duplicados por (liga, fecha, local, visitante).",
                {"count": len(dupes), "example": str(next(iter(dupes)))},
            )
        ]
    return []


def check_edge_distribution(edges: Sequence[float]) -> list[SanityFinding]:
    """Distribución de edge sistemáticamente positiva.

    Sobre el conjunto de candidates, un modelo calibrado produce edges centrados
    cerca de 0: para cada lado con edge positivo, el contrario debería tenerlo
    negativo. Una media muy positiva casi nunca significa ventaja — significa que
    el vig no se está retirando bien, o que se compara contra la implied.
    """
    if len(edges) < MIN_CANDIDATES_FOR_EDGE_CHECK:
        return []
    mean_edge = sum(edges) / len(edges)
    if mean_edge > SUSPICIOUS_MEAN_EDGE:
        return [
            SanityFinding(
                "edge_distribution",
                Severity.FATAL,
                f"Edge medio {mean_edge:+.2%} sobre {len(edges)} candidates. Un modelo "
                "calibrado centra el edge cerca de 0. Sospechar error de vig o "
                "comparación contra la implied en vez de la fair probability.",
                {"mean_edge": mean_edge, "n": len(edges)},
            )
        ]
    return []


def check_closing_coverage(captured: int, total: int) -> list[SanityFinding]:
    """Cobertura de closing lines sobre el slate.

    Por debajo del umbral la validación sin apuestas (ARCHITECTURE.md §4.6) pierde
    potencia y, peor, se sesga: los eventos que sí se capturaron no son una
    muestra aleatoria de los que hubo.
    """
    if total <= 0:
        return []
    coverage = captured / total
    if coverage < MIN_CLOSING_COVERAGE:
        return [
            SanityFinding(
                "closing_coverage",
                Severity.WARNING,
                f"Cobertura de cierres {coverage:.1%} ({captured}/{total}), por debajo "
                f"del {MIN_CLOSING_COVERAGE:.0%}. La validación pierde potencia y se "
                "sesga hacia los eventos capturados. La muestra perdida es irrecuperable.",
                {"coverage": coverage, "captured": captured, "total": total},
            )
        ]
    return []


# --- Runner -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BacktestSample:
    """Todo lo que los checks necesitan de un backtest, sin tocar la base de datos."""

    n_bets: int
    roi: float
    win_rate: float
    avg_decimal_odds: float
    edges: Sequence[float] = ()
    feature_as_of_pairs: Sequence[tuple[datetime, datetime]] = ()
    odds_capture_pairs: Sequence[tuple[datetime, datetime]] = ()
    markets: Sequence[tuple[Hashable, Sequence[float]]] = ()
    event_keys: Sequence[Hashable] = ()
    closing_captured: int = 0
    closing_total: int = 0


def run_sanity_checks(sample: BacktestSample) -> SanityReport:
    """Ejecuta todos los checks. `report.passed == False` bloquea las métricas."""
    findings: list[SanityFinding] = []
    findings += check_feature_leakage(sample.feature_as_of_pairs)
    findings += check_odds_after_start(sample.odds_capture_pairs)
    findings += check_duplicate_events(sample.event_keys)
    findings += check_market_overround(sample.markets)
    findings += check_edge_distribution(sample.edges)
    findings += check_roi_vs_sample_size(sample.n_bets, sample.roi)
    findings += check_win_rate(sample.n_bets, sample.win_rate, sample.avg_decimal_odds)
    findings += check_closing_coverage(sample.closing_captured, sample.closing_total)
    return SanityReport(findings)
