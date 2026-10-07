"""Normalización de nombres de entidades.

El módulo que todos subestiman. "NY Yankees", "New York Yankees", "NYY" y
"Yankees" son la misma entidad para un humano y cuatro strings distintos para un
`==`. Emparejar mal un equipo no produce un error: produce un evento duplicado,
o un precio colgado de otro partido, y eso sale del otro lado como edge que no
existe.
"""

from __future__ import annotations

import re
import unicodedata

# Sufijos corporativos y ruido que los proveedores añaden de forma inconsistente.
_NOISE_TOKENS = frozenset({"fc", "sc", "cf", "the"})
_PUNCTUATION = re.compile(r"[^\w\s]", flags=re.UNICODE)
_WHITESPACE = re.compile(r"\s+")


def strip_accents(text: str) -> str:
    """Quita diacríticos. 'Montréal' -> 'Montreal'."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalize_name(raw: str) -> str:
    """Forma canónica para comparar nombres.

    Minúsculas, sin acentos, sin puntuación, espacios colapsados y sin tokens de
    ruido. No intenta ser inteligente: la inteligencia va en el resolver, que
    puede consultar alias persistidos. Aquí solo se quita variación tipográfica.

    >>> normalize_name("St. Louis Cardinals")
    'st louis cardinals'
    >>> normalize_name("  Montréal   Expos ")
    'montreal expos'
    """
    text = strip_accents(raw).lower()
    text = _PUNCTUATION.sub(" ", text)
    text = _WHITESPACE.sub(" ", text).strip()
    tokens = [t for t in text.split(" ") if t and t not in _NOISE_TOKENS]
    return " ".join(tokens)


def name_tokens(raw: str) -> frozenset[str]:
    """Conjunto de tokens normalizados, para comparaciones parciales."""
    return frozenset(normalize_name(raw).split())


def token_overlap(a: str, b: str) -> float:
    """Similitud de Jaccard entre los tokens de dos nombres, en [0, 1].

    Se usa como último recurso y siempre con umbral alto. Un emparejamiento
    dudoso es peor que ninguno: el que no empareja acaba en la cola de revisión
    y se ve; el que empareja mal se convierte en un dato silenciosamente falso.
    """
    ta, tb = name_tokens(a), name_tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)
