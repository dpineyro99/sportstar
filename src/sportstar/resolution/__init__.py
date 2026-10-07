"""Emparejamiento de entidades entre proveedores y catálogo."""

from .names import normalize_name, strip_accents, token_overlap
from .resolver import MIN_TOKEN_OVERLAP, UNRESOLVED, Resolution, TeamResolver

__all__ = [
    "MIN_TOKEN_OVERLAP",
    "UNRESOLVED",
    "Resolution",
    "TeamResolver",
    "normalize_name",
    "strip_accents",
    "token_overlap",
]
