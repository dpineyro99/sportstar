"""Retirada del vig: implied probability -> fair (no-vig) probability.

Un mercado de dos lados a -110/-110 tiene implied 0.5238 + 0.5238 = 1.0476.
Ese 4.76% sobrante es el margen del book. Comparar el modelo contra la implied
sin retirar el vig produce edge negativo sistemático de ~2.4 puntos por lado.

Tres métodos, porque **no son equivalentes y la elección tiene consecuencias**:

- `PROPORTIONAL` — divide cada implied por el overround. Asume que el vig se
  reparte proporcionalmente entre los lados.
- `POWER` — resuelve `Σ pᵢ^k = 1`. Reparte el vig de forma no lineal.
- `SHIN` — modela una fracción `z` de apostadores informados. Es el que corrige
  el favorite-longshot bias de forma explícita.

**Por qué importa la elección.** Empíricamente los books cargan más margen en el
underdog, porque el público sobreapuesta longshots. El método proporcional deja
esa distorsión intacta: sobreestima la probabilidad justa del underdog y, por
tanto, **subestima la del favorito**. Como `edge = model_prob - fair_prob`, el
efecto neto es *edge fantasma en los favoritos*. Es sutil, es sistemático, y no
lo detecta ningún test de humo — solo verlo escrito y medirlo.

`PROPORTIONAL` es el default de v1 por simplicidad y por ser el estándar de
facto, con el sesgo documentado. La elección definitiva se decide midiendo cuál
predice mejor el resultado real contra closing lines históricos (ROADMAP Phase 3),
no por preferencia.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum

from .errors import InvalidMarketError
from .odds import overround, validate_probability

_TOL = 1e-12
_MAX_ITER = 200


class NoVigMethod(StrEnum):
    """Métodos de retirada de vig. El valor se persiste en `candidates.novig_method`."""

    PROPORTIONAL = "proportional"
    POWER = "power"
    SHIN = "shin"


def _bisect(f: Callable[[float], float], lo: float, hi: float) -> float:
    """Bisección sobre una función monótona con raíz garantizada en [lo, hi]."""
    f_lo, f_hi = f(lo), f(hi)
    if f_lo * f_hi > 0:
        raise InvalidMarketError(
            f"no hay raíz en [{lo}, {hi}]: f(lo)={f_lo:.6g}, f(hi)={f_hi:.6g}. "
            "Suele indicar un mercado mal formado o precios de selecciones distintas."
        )
    for _ in range(_MAX_ITER):
        mid = (lo + hi) / 2.0
        f_mid = f(mid)
        if abs(f_mid) < _TOL or (hi - lo) < _TOL:
            return mid
        if f_lo * f_mid <= 0:
            hi = mid
        else:
            lo, f_lo = mid, f_mid
    # Inalcanzable con los valores actuales: la bisección halva el intervalo en
    # cada vuelta, así que `hi - lo < _TOL` se cumple en ~40 iteraciones de las
    # 200 disponibles. Se mantiene como red de seguridad por si alguien sube
    # _TOL o baja _MAX_ITER: preferimos un número acotado a un bucle infinito en
    # un worker de producción.
    return (lo + hi) / 2.0  # pragma: no cover


def _normalize(values: list[float]) -> list[float]:
    """Renormaliza a suma exacta 1.0, absorbiendo el error de la tolerancia."""
    total = sum(values)
    return [v / total for v in values]


def _validate_market(probabilities: list[float]) -> list[float]:
    probs = [validate_probability(p, name=f"selección {i}") for i, p in enumerate(probabilities)]
    total = overround(probs)
    if total <= 1.0:
        raise InvalidMarketError(
            f"overround {total:.6f} <= 1: el mercado no tiene vig. Con precios reales esto "
            "es casi siempre un precio corrupto o dos lados que no pertenecen al mismo "
            "mercado, no un arbitraje."
        )
    return probs


def remove_vig_proportional(probabilities: list[float]) -> list[float]:
    """Reparto proporcional. Sesga a favor del underdog (ver módulo)."""
    probs = _validate_market(probabilities)
    return _normalize(probs)


def remove_vig_power(probabilities: list[float]) -> list[float]:
    """Resuelve `Σ pᵢ^k = 1` con k > 1."""
    probs = _validate_market(probabilities)

    def f(k: float) -> float:
        # float() explícito: para mypy `float ** float` es Any, porque una base
        # negativa con exponente fraccionario daría complex. Aquí la base es
        # siempre una probabilidad en (0, 1), así que el resultado es real.
        return float(sum(p**k for p in probs)) - 1.0

    # f es decreciente en k: f(1) = overround - 1 > 0. Se amplía hi hasta cruzar 0.
    hi = 2.0
    while f(hi) > 0 and hi < 1e6:
        hi *= 2.0
    k = _bisect(f, 1.0, hi)
    return _normalize([p**k for p in probs])


def remove_vig_shin(probabilities: list[float]) -> list[float]:
    """Método de Shin (1993).

    Modela el precio como el resultado de un book que se protege frente a una
    fracción `z` de apostadores informados. Corrige el favorite-longshot bias:
    devuelve al favorito una probabilidad justa **mayor** que el proporcional, y
    al underdog una **menor**.
    """
    probs = _validate_market(probabilities)
    total = sum(probs)
    c = [p * p / total for p in probs]

    def q_of(z: float) -> list[float]:
        denom = 2.0 * (1.0 - z)
        return [((z * z + 4.0 * (1.0 - z) * ci) ** 0.5 - z) / denom for ci in c]

    def f(z: float) -> float:
        return sum(q_of(z)) - 1.0

    # f(0) = sqrt(overround) - 1 > 0; decrece con z. 1 - 1e-9 evita el 0/0 en z = 1.
    z = _bisect(f, 0.0, 1.0 - 1e-9)
    return _normalize(q_of(z))


_METHODS: dict[NoVigMethod, Callable[[list[float]], list[float]]] = {
    NoVigMethod.PROPORTIONAL: remove_vig_proportional,
    NoVigMethod.POWER: remove_vig_power,
    NoVigMethod.SHIN: remove_vig_shin,
}


def remove_vig(
    probabilities: list[float],
    method: NoVigMethod = NoVigMethod.PROPORTIONAL,
) -> list[float]:
    """Retira el vig de un mercado completo. Devuelve probabilidades que suman 1.

    Requiere **todas** las selecciones del mercado. Con un solo lado no se puede
    saber cuánto margen lleva el precio, y estimarlo con una constante es una
    invención que se propaga hasta el edge.
    """
    return _METHODS[NoVigMethod(method)](probabilities)


def shin_z(probabilities: list[float]) -> float:
    """Fracción implícita de dinero informado según Shin. Diagnóstico de mercado.

    Valores altos señalan un mercado que el book considera arriesgado. Útil como
    feature y como check de calidad: un `z` disparado suele acompañar a precios
    stale o a un emparejamiento incorrecto de selecciones.
    """
    probs = _validate_market(probabilities)
    total = sum(probs)
    c = [p * p / total for p in probs]

    def f(z: float) -> float:
        denom = 2.0 * (1.0 - z)
        return float(sum(((z * z + 4.0 * (1.0 - z) * ci) ** 0.5 - z) / denom for ci in c)) - 1.0

    return _bisect(f, 0.0, 1.0 - 1e-9)
