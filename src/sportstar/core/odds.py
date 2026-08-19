"""Conversión entre formatos de cuota y probabilidad implícita.

Funciones puras, sin I/O. Toda la aritmética de precios del sistema pasa por
aquí para que exista un único sitio donde equivocarse — y un único sitio donde
los tests lo impidan.

Vocabulario (ver ARCHITECTURE.md §4):

- **implied probability**: 1/decimal. Incluye el vig. NO es una probabilidad
  real: en un mercado de dos lados las dos implied suman > 1.
- **fair / no-vig probability**: la implied tras retirar el vig (novig.py).
- **break-even probability**: idéntica a la implied. Es el porcentaje de aciertos
  necesario para no perder dinero a ese precio.

Confundir la primera con la segunda infla el edge de forma sistemática y es el
error más común del dominio.
"""

from __future__ import annotations

from .errors import InvalidOddsError, InvalidProbabilityError

# Las cuotas americanas no existen entre -100 y +100 (exclusive): ese rango
# describiría un pago inferior al riesgo en ambas direcciones.
MIN_ABS_AMERICAN = 100.0


def validate_probability(p: float, *, name: str = "probability") -> float:
    """Valida que `p` esté en (0, 1) abierto.

    Los extremos se rechazan a propósito: una probabilidad de 0 o 1 implica
    certeza, y en la práctica siempre significa un dato corrupto o una división
    que se va a infinito más adelante.
    """
    if not isinstance(p, int | float) or p != p:  # NaN != NaN
        raise InvalidProbabilityError(f"{name} no es un número: {p!r}")
    if not 0.0 < p < 1.0:
        raise InvalidProbabilityError(f"{name} debe estar en (0, 1), recibido {p!r}")
    return float(p)


def american_to_decimal(american: float) -> float:
    """Convierte cuota americana a decimal.

    >>> round(american_to_decimal(-115), 6)
    1.869565
    >>> american_to_decimal(150)
    2.5
    """
    a = float(american)
    if a != a:
        raise InvalidOddsError(f"cuota americana no es un número: {american!r}")
    if abs(a) < MIN_ABS_AMERICAN:
        raise InvalidOddsError(
            f"cuota americana inválida: {american!r} (|valor| debe ser >= {MIN_ABS_AMERICAN:.0f})"
        )
    if a > 0:
        return 1.0 + a / 100.0
    return 1.0 + 100.0 / abs(a)


def decimal_to_american(decimal_odds: float) -> float:
    """Convierte cuota decimal a americana.

    En decimal 2.0 (pick'em) la convención devuelve +100.
    """
    d = validate_decimal(decimal_odds)
    if d >= 2.0:
        return (d - 1.0) * 100.0
    return -100.0 / (d - 1.0)


def validate_decimal(decimal_odds: float) -> float:
    """Valida una cuota decimal: debe ser > 1.0."""
    d = float(decimal_odds)
    if d != d:
        raise InvalidOddsError(f"cuota decimal no es un número: {decimal_odds!r}")
    if d <= 1.0:
        raise InvalidOddsError(f"cuota decimal debe ser > 1.0, recibido {decimal_odds!r}")
    return d


def decimal_to_implied(decimal_odds: float) -> float:
    """Probabilidad implícita (CON vig) de una cuota decimal."""
    return 1.0 / validate_decimal(decimal_odds)


def implied_to_decimal(p: float) -> float:
    """Cuota decimal que corresponde a una probabilidad."""
    return 1.0 / validate_probability(p)


def american_to_implied(american: float) -> float:
    """Probabilidad implícita (CON vig) de una cuota americana."""
    return decimal_to_implied(american_to_decimal(american))


def implied_to_american(p: float) -> float:
    """Cuota americana que corresponde a una probabilidad."""
    return decimal_to_american(implied_to_decimal(p))


def break_even_probability(decimal_odds: float) -> float:
    """Porcentaje de aciertos necesario para no perder dinero a este precio.

    Numéricamente idéntica a la implied probability. Existe como función aparte
    porque es lo que se muestra en el dashboard como "Market Break-even", y
    nombrarla bien evita que alguien la lea como la probabilidad real del evento.
    """
    return decimal_to_implied(decimal_odds)


def overround(probabilities: list[float]) -> float:
    """Suma de las implied de un mercado. El vig es `overround - 1`.

    Un mercado sano tiene overround > 1. Por debajo de 1 hay arbitraje aparente,
    que en la práctica casi siempre es un precio corrupto o dos lados que no
    pertenecen al mismo mercado — no una oportunidad.
    """
    if len(probabilities) < 2:
        raise InvalidProbabilityError("el overround requiere al menos 2 selecciones")
    return sum(validate_probability(p, name=f"selección {i}") for i, p in enumerate(probabilities))


def vig(probabilities: list[float]) -> float:
    """Margen del book, expresado como fracción. `-110/-110` ≈ 0.0476."""
    return overround(probabilities) - 1.0
