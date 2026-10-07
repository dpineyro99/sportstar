"""Excepciones del núcleo matemático.

El núcleo falla ruidosamente. Un precio corrupto o una probabilidad imposible
nunca se degrada a un valor por defecto: cualquier valor por defecto se propaga
en silencio hasta el edge y contamina todo aguas abajo.
"""


class CoreError(ValueError):
    """Base de los errores del núcleo."""


class InvalidOddsError(CoreError):
    """Precio fuera del dominio válido."""


class InvalidProbabilityError(CoreError):
    """Probabilidad fuera de (0, 1) o conjunto que no suma lo que debería."""


class InvalidMarketError(CoreError):
    """Mercado mal formado: pocos lados, overround imposible, etc."""
