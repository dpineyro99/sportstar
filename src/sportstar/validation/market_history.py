"""Auditoría de un histórico de mercado antes de dejarlo entrar al backtest.

Un dataset histórico descargado de un tercero es la entrada de la que dependen
todas las conclusiones posteriores. Si viene roto y no lo detectamos, el backtest
no falla: **da un número**, y ese número parece un resultado. Por eso el histórico
se audita antes de usarse, con checks que no miden calidad sino posibilidad
física.

Los cuatro checks bloqueantes:

1. **Sobre-redondeo plausible.** Un mercado de dos vías con vig negativo es
   imposible; con vig del 20% no es un mercado, es un error de emparejamiento.
2. **Tasa de victoria local.** MLB está en 53-54% desde hace décadas. Un 48% dice
   que local y visitante están cruzados en parte del fichero.
3. **Calibración del cierre.** La línea de cierre sin vig es el mejor estimador
   público que existe: tiene que salir casi perfectamente calibrada. Si no lo
   está, los precios no corresponden a esos partidos.
4. **El cierre gana a la apertura.** Es el resultado más robusto de la literatura
   —el mercado incorpora información entre apertura y cierre—. Si la apertura
   predice mejor, lo más probable es que las columnas estén intercambiadas.

El cuarto es el que más veces salva: es una comprobación *interna*, no necesita
ninguna fuente externa contra la que contrastar.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from statistics import median

from ..core.novig import NoVigMethod, remove_vig
from ..core.odds import american_to_implied
from .calibration import CalibrationReport, evaluate
from .sanity import SanityFinding, SanityReport, Severity

# Horquilla de sobre-redondeo aceptable en la mediana de un histórico de
# moneyline. Por debajo de 1% no es un mercado real; por encima de 8% no es un
# mercado de dos vías bien emparejado.
ACCEPTABLE_MEDIAN_OVERROUND = (0.01, 0.08)

# Fracción máxima de partidos con sobre-redondeo fuera de rango físico.
MAX_IMPLAUSIBLE_OVERROUND_RATE = 0.02

# Ventaja local en MLB: histórico estable en torno al 53-54%. La horquilla es
# ancha a propósito; su función es cazar un cruce local/visitante, no medir.
PLAUSIBLE_HOME_WIN_RATE = (0.50, 0.58)

# Error de calibración máximo tolerable en la línea de cierre. El cierre de un
# mercado líquido calibra por debajo de 0,01; 0,03 ya es señal de que los precios
# no son de esos partidos.
MAX_CLOSING_CALIBRATION_ERROR = 0.03

MIN_GAMES_FOR_AUDIT = 200


@dataclass(frozen=True, slots=True)
class MarketSample:
    """Un partido histórico reducido a lo que la auditoría necesita.

    Precios en probabilidad implícita **con vig**, tal como llegaron. La
    auditoría quita el vig ella misma: quién lo quitó y con qué método es
    justamente una de las cosas que puede estar mal.
    """

    home_open_implied: float | None
    away_open_implied: float | None
    home_close_implied: float | None
    away_close_implied: float | None
    home_won: bool


def _fair_home(home: float | None, away: float | None, method: NoVigMethod) -> float | None:
    if home is None or away is None:
        return None
    total = home + away
    if total <= 1.0:
        # Sin vig o con vig negativo: quitarle vig a esto no tiene sentido, y
        # forzarlo enmascararía justo el dato que delata el problema.
        return None
    return remove_vig([home, away], method=method)[0]


@dataclass(frozen=True, slots=True)
class MarketHistoryAudit:
    """Lo que se sabe del histórico tras mirarlo con desconfianza."""

    n_games: int
    n_with_close: int
    n_with_open: int
    home_win_rate: float
    median_overround: float
    implausible_overround_rate: float
    closing: CalibrationReport
    opening: CalibrationReport | None
    report: SanityReport

    @property
    def closing_beats_opening(self) -> bool | None:
        """¿El cierre predice mejor que la apertura? Debe ser `True`."""
        if self.opening is None:
            return None
        return self.closing.brier < self.opening.brier

    def summary(self) -> str:
        lines = [
            f"partidos                {self.n_games}",
            f"con cierre / apertura   {self.n_with_close} / {self.n_with_open}",
            f"% victoria local        {self.home_win_rate:.4f}",
            f"sobre-redondeo mediano  {self.median_overround * 100:.2f}%",
            f"vig implausible         {self.implausible_overround_rate:.4f}",
            f"Brier cierre            {self.closing.brier:.4f}"
            f"  (ECE {self.closing.calibration_error:.4f})",
        ]
        if self.opening is not None:
            lines.append(
                f"Brier apertura          {self.opening.brier:.4f}"
                f"  (ECE {self.opening.calibration_error:.4f})"
            )
            lines.append(f"cierre mejora apertura  {self.closing_beats_opening}")
        for finding in self.report.findings:
            lines.append(f"[{finding.severity.value.upper()}] {finding.check}: {finding.message}")
        return "\n".join(lines)


def audit(
    samples: Sequence[MarketSample], *, method: NoVigMethod = NoVigMethod.PROPORTIONAL
) -> MarketHistoryAudit:
    """Audita un histórico. Los hallazgos `FATAL` significan: no usar este fichero."""
    if len(samples) < MIN_GAMES_FOR_AUDIT:
        raise ValueError(
            f"hacen falta al menos {MIN_GAMES_FOR_AUDIT} partidos para auditar un "
            f"histórico; llegaron {len(samples)}. Con menos, cualquier desviación "
            "cabe dentro del ruido y el check no distingue nada."
        )

    findings: list[SanityFinding] = []
    home_win_rate = sum(s.home_won for s in samples) / len(samples)

    overrounds = [
        s.home_close_implied + s.away_close_implied - 1.0
        for s in samples
        if s.home_close_implied is not None and s.away_close_implied is not None
    ]
    low, high = ACCEPTABLE_MEDIAN_OVERROUND
    median_ov = median(overrounds) if overrounds else 0.0
    implausible = (
        sum(1 for o in overrounds if o <= 0.0 or o > 0.15) / len(overrounds) if overrounds else 1.0
    )

    close_pairs = [
        (p, int(s.home_won))
        for s in samples
        if (p := _fair_home(s.home_close_implied, s.away_close_implied, method)) is not None
    ]
    open_pairs = [
        (p, int(s.home_won))
        for s in samples
        if (p := _fair_home(s.home_open_implied, s.away_open_implied, method)) is not None
    ]
    if not close_pairs:
        raise ValueError("ningún partido tiene línea de cierre utilizable; no hay nada que auditar")

    closing = evaluate([p for p, _ in close_pairs], [y for _, y in close_pairs])
    opening = (
        evaluate([p for p, _ in open_pairs], [y for _, y in open_pairs]) if open_pairs else None
    )

    if not low <= median_ov <= high:
        findings.append(
            SanityFinding(
                check="median_overround",
                severity=Severity.FATAL,
                message=(
                    f"el sobre-redondeo mediano es {median_ov * 100:.2f}%, fuera de "
                    f"[{low * 100:.0f}%, {high * 100:.0f}%]. Un moneyline de dos vías no "
                    "se comporta así: lo habitual es que los dos precios no sean del "
                    "mismo partido."
                ),
                detail={"median_overround": median_ov},
            )
        )
    if implausible > MAX_IMPLAUSIBLE_OVERROUND_RATE:
        findings.append(
            SanityFinding(
                check="implausible_overround",
                severity=Severity.FATAL,
                message=(
                    f"{implausible:.1%} de los partidos tienen un sobre-redondeo "
                    "imposible (negativo o >15%). Dos favoritos en un mercado de dos "
                    "vías es un error de emparejamiento, no un mercado."
                ),
                detail={"rate": implausible},
            )
        )
    lo_hw, hi_hw = PLAUSIBLE_HOME_WIN_RATE
    if not lo_hw <= home_win_rate <= hi_hw:
        findings.append(
            SanityFinding(
                check="home_win_rate",
                severity=Severity.FATAL,
                message=(
                    f"el local gana el {home_win_rate:.1%} de los partidos, fuera de "
                    f"[{lo_hw:.0%}, {hi_hw:.0%}]. La ventaja local de MLB lleva décadas "
                    "en el 53-54%; salirse de ahí apunta a local y visitante cruzados."
                ),
                detail={"home_win_rate": home_win_rate},
            )
        )
    if closing.calibration_error > MAX_CLOSING_CALIBRATION_ERROR:
        findings.append(
            SanityFinding(
                check="closing_calibration",
                severity=Severity.FATAL,
                message=(
                    f"la línea de cierre sin vig calibra con un error de "
                    f"{closing.calibration_error:.4f}, por encima de "
                    f"{MAX_CLOSING_CALIBRATION_ERROR}. El cierre de un mercado líquido "
                    "es el mejor estimador público que hay: si no calibra, los precios "
                    "no son de estos partidos."
                ),
                detail={"calibration_error": closing.calibration_error},
            )
        )
    if opening is not None and closing.brier >= opening.brier:
        findings.append(
            SanityFinding(
                check="closing_beats_opening",
                severity=Severity.FATAL,
                message=(
                    f"la apertura predice igual o mejor que el cierre "
                    f"(Brier {opening.brier:.4f} vs {closing.brier:.4f}). El mercado "
                    "incorpora información entre apertura y cierre; ver lo contrario "
                    "apunta a que las dos columnas están intercambiadas."
                ),
                detail={"brier_open": opening.brier, "brier_close": closing.brier},
            )
        )

    return MarketHistoryAudit(
        n_games=len(samples),
        n_with_close=len(close_pairs),
        n_with_open=len(open_pairs),
        home_win_rate=home_win_rate,
        median_overround=median_ov,
        implausible_overround_rate=implausible,
        closing=closing,
        opening=opening,
        report=SanityReport(findings=findings),
    )


def samples_from_american(
    rows: Sequence[tuple[float | None, float | None, float | None, float | None, bool]],
) -> list[MarketSample]:
    """Atajo: convierte (open_home, open_away, close_home, close_away, ganó_local)."""

    def implied(value: float | None) -> float | None:
        return None if value is None or value == 0.0 else american_to_implied(value)

    return [
        MarketSample(
            home_open_implied=implied(oh),
            away_open_implied=implied(oa),
            home_close_implied=implied(ch),
            away_close_implied=implied(ca),
            home_won=won,
        )
        for oh, oa, ch, ca, won in rows
    ]
