"""Errores de forma de payload, con diagnóstico útil.

Filosofía del módulo: cuando el proveedor cambia el formato —y lo hará— el error
debe decir **exactamente** qué faltaba y qué llegó en su lugar. La alternativa es
un `matched: 0` sin explicación, que es la forma más cara de descubrir un cambio
de esquema.

Estos normalizadores están escritos contra documentación, no contra respuestas
verificadas. Esa es precisamente la razón de invertir en mensajes de error
buenos: la primera ejecución real es la verificación, y debe ser barata de
interpretar.
"""

from __future__ import annotations

from typing import Any


class ShapeError(ValueError):
    """El payload no tiene la forma esperada."""


def _describe(value: Any) -> str:
    if isinstance(value, dict):
        keys = list(value)[:12]
        suffix = ", ..." if len(value) > 12 else ""
        return f"dict con claves [{', '.join(map(repr, keys))}{suffix}]"
    if isinstance(value, list):
        return f"lista de {len(value)} elemento(s)"
    return f"{type(value).__name__} ({value!r})"


def require_dict(value: Any, *, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ShapeError(f"{path}: se esperaba un objeto, llegó {_describe(value)}")
    return value


def require_list(value: Any, *, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise ShapeError(f"{path}: se esperaba una lista, llegó {_describe(value)}")
    return value


def require_key(payload: dict[str, Any], key: str, *, path: str) -> Any:
    if key not in payload:
        raise ShapeError(
            f"{path}: falta la clave {key!r}. Llegó {_describe(payload)}. "
            "Suele significar que el proveedor cambió el formato."
        )
    return payload[key]


def require_str(payload: dict[str, Any], key: str, *, path: str) -> str:
    value = require_key(payload, key, path=path)
    if not isinstance(value, str) or not value.strip():
        raise ShapeError(f"{path}.{key}: se esperaba un texto no vacío, llegó {_describe(value)}")
    return value


def require_number(payload: dict[str, Any], key: str, *, path: str) -> float:
    value = require_key(payload, key, path=path)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ShapeError(f"{path}.{key}: se esperaba un número, llegó {_describe(value)}")
    return float(value)


def optional_number(payload: dict[str, Any], key: str, *, path: str) -> float | None:
    value = payload.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ShapeError(
            f"{path}.{key}: se esperaba un número u omitirlo, llegó {_describe(value)}"
        )
    return float(value)
